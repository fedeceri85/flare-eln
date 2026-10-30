"""Usage is attributed to the actor and visible only through read-only admin."""
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import transaction
from django.test import override_settings
from django.urls import reverse

from ..models import Experiment, Mouse, Project, Protocol, Recording, RecordingType, UsageEvent
from ..sharing import share_animals, share_recording
from ._support import AuthenticatedTestCase


@override_settings(ALLOWED_HOSTS=["testserver"])
class UsageEventTests(AuthenticatedTestCase):
    def setUp(self):
        protocol = Protocol.objects.create(protocol_number=191, licence_reference="USAGE", name="Usage")
        self.mouse = Mouse.objects.create(mouse_id="Usage mouse", protocol=protocol)
        kind = RecordingType.objects.create(name="Usage type", slug="usage-type")
        project = Project.objects.create(name="Usage project")
        self.experiment = Experiment.objects.create(project=project, name="Usage experiment", recording_type=kind)
        self.recording = Recording.objects.create(experiment=self.experiment, recording_type=kind, owner=self.user, recording_date=date(2026, 10, 30))
        self.animal_url = reverse("lab:animal_report")
        self.recording_url = reverse("lab:recording_report", args=[project.pk, self.experiment.pk])

    def test_new_and_reused_codes_record_the_actor_and_keep_original_creator(self):
        first = share_animals([self.mouse.pk], "", self.user)
        other = get_user_model().objects.create_user(username="usage-other")
        reused = share_animals([self.mouse.pk], "", other)
        self.assertEqual(first.pk, reused.pk)
        self.assertEqual(reused.created_by, self.user)
        events = list(UsageEvent.objects.order_by("pk"))
        self.assertEqual([event.event_type for event in events], [UsageEvent.CODE_CREATED, UsageEvent.CODE_REUSED])
        self.assertEqual([event.user for event in events], [self.user, other])
        for event in events:
            self.assertEqual(event.reference, first.code)
            self.assertEqual(event.target_type, "animals")
            self.assertEqual(event.object_count, 1)
            self.assertEqual(event.source, "sharing")
        other.delete()
        events[1].refresh_from_db()
        self.assertIsNone(events[1].user)
        self.assertEqual(events[1].username, "usage-other")

    def test_recording_codes_and_reuse_are_logged(self):
        link = share_recording(self.recording.pk, self.user)
        share_recording(self.recording.pk, self.user)
        self.assertEqual(UsageEvent.objects.filter(event_type=UsageEvent.CODE_CREATED).count(), 1)
        event = UsageEvent.objects.get(event_type=UsageEvent.CODE_REUSED)
        self.assertEqual((event.reference, event.target_type, event.object_count), (link.code, "recording", 1))

    def test_rolled_back_code_allocation_does_not_leave_an_event(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                share_animals([self.mouse.pk], "", self.user)
                raise RuntimeError("rollback")
        self.assertFalse(UsageEvent.objects.exists())

    def test_animal_exports_record_each_format_and_code_source(self):
        for output_format in ("pdf", "xlsx"):
            response = self.client.post(self.animal_url, {"selected_mouse": self.mouse.pk, "format": output_format})
            self.assertEqual(response.status_code, 200)
        exports = UsageEvent.objects.filter(event_type=UsageEvent.REPORT_EXPORTED).order_by("pk")
        self.assertEqual([event.format for event in exports], ["pdf", "xlsx"])
        for event in exports:
            self.assertEqual(event.user, self.user)
            self.assertEqual(event.username, self.user.get_username())
            self.assertEqual(event.object_count, 1)
            self.assertEqual(event.target_type, "animals")
        self.assertEqual(UsageEvent.objects.get(event_type=UsageEvent.CODE_CREATED).source, "report")
        self.assertEqual(UsageEvent.objects.get(event_type=UsageEvent.CODE_REUSED).source, "report")

    def test_experiment_exports_record_actor_experiment_and_row_count(self):
        for output_format in ("pdf", "xlsx"):
            response = self.client.post(self.recording_url, {"download": "1", "format": output_format, "scope": "all"})
            self.assertEqual(response.status_code, 200)
        self.assertEqual(UsageEvent.objects.count(), 2)
        for event in UsageEvent.objects.all():
            self.assertEqual(event.event_type, UsageEvent.REPORT_EXPORTED)
            self.assertEqual(event.user, self.user)
            self.assertEqual(event.reference, str(self.experiment.pk))
            self.assertEqual(event.target_type, "experiment")
            self.assertEqual(event.object_count, 1)

    def test_configuration_invalid_requests_and_failed_render_are_not_exports(self):
        self.client.get(self.animal_url, {"selected_mouse": self.mouse.pk})
        self.client.post(self.animal_url, {"selected_mouse": self.mouse.pk, "format": "invalid"})
        self.client.post(self.recording_url, {"download": "1", "format": "pdf", "scope": "selected"})
        self.assertFalse(UsageEvent.objects.exists())
        with patch("lab.views.animal_reports.render_pdf", side_effect=RuntimeError("render failed")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.animal_url, {"selected_mouse": self.mouse.pk, "format": "pdf"})
        self.assertFalse(UsageEvent.objects.filter(event_type=UsageEvent.REPORT_EXPORTED).exists())

    def test_usage_admin_requires_view_permission_and_is_read_only(self):
        link = share_animals([self.mouse.pk], "", self.user)
        event = UsageEvent.objects.get(reference=link.code)
        url = reverse("admin:lab_usageevent_changelist")
        detail = reverse("admin:lab_usageevent_change", args=[event.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.user.user_permissions.add(Permission.objects.get(codename="view_usageevent"))
        self.assertContains(self.client.get(url), link.code)
        self.assertContains(self.client.get(detail), self.user.get_username())
        for action in ("add", "change", "delete"):
            self.user.user_permissions.add(Permission.objects.get(codename=f"{action}_usageevent"))
        self.assertEqual(self.client.post(detail, {"username": "changed"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:lab_usageevent_add")).status_code, 403)
        self.assertEqual(self.client.post(reverse("admin:lab_usageevent_delete", args=[event.pk]), {"post": "yes"}).status_code, 403)
        event.refresh_from_db()
        self.assertEqual(event.username, self.user.get_username())
        response = self.client.get(url, {"event_type__exact": UsageEvent.CODE_REUSED})
        self.assertNotContains(response, link.code)
