from datetime import date
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Experiment, Mouse, Project, Protocol, Recording, RecordingField, RecordingType


@override_settings(ALLOWED_HOSTS=["testserver"])
class RecordingDraftTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="draft-test")
        self.client.force_login(self.user)
        recording_type = RecordingType.objects.create(name="Draft test", slug="draft-test")
        project = Project.objects.create(name="Draft test")
        self.experiment = Experiment.objects.create(
            name="Draft test", project=project, recording_type=recording_type,
        )
        self.custom = RecordingField.objects.create(
            recording_type=recording_type, key="custom", label="Custom", data_type="text",
        )
        protocol = Protocol.objects.create(protocol_number=1, licence_reference="TEST", name="Test")
        self.mouse = Mouse.objects.create(mouse_id="DRAFT-MOUSE", protocol=protocol)
        self.add_url = reverse("lab:add_recording", args=[project.pk, self.experiment.pk])
        self.recording = Recording.objects.create(
            experiment=self.experiment, recording_type=recording_type, owner=self.user,
            recording_date=date(2026, 1, 1), notes="Saved notes",
        )
        self.edit_url = reverse(
            "lab:edit_recording", args=[project.pk, self.experiment.pk, self.recording.pk],
        )

    def round_trip(self, url, data):
        response = self.client.post(url, {**data, "recording_action": "select_mice"})
        self.assertEqual(response.status_code, 302)
        selector_url = response.url
        return_url = parse_qs(urlsplit(selector_url).query)["return_url"][0]
        response = self.client.post(selector_url, {
            "return_url": return_url, "selected_mouse": [self.mouse.pk],
        })
        self.assertEqual(response.status_code, 302)
        return response.url, self.client.get(response.url)

    def test_add_and_edit_preserve_all_fields_and_do_not_save_early(self):
        for url in (self.add_url, self.edit_url):
            with self.subTest(url=url):
                data = {
                    "recording_date": "2026-10-21",
                    "notes": "Unfinished notes", "data_paths": "/path/one\n/path/two",
                    f"value_{self.custom.pk}": "Custom entry",
                }
                resumed_url, response = self.round_trip(url, data)
                self.assertEqual(response.status_code, 200)
                form = response.context["form"]
                self.assertFalse(form.is_bound)
                for name, value in data.items():
                    self.assertEqual(form[name].value(), value)
                self.assertEqual(form.selected_mouse_ids(), [self.mouse.pk])
                self.assertContains(response, 'value="2026-10-21"')
                self.assertEqual(Recording.objects.count(), 1)
                self.recording.refresh_from_db()
                self.assertEqual(self.recording.notes, "Saved notes")
                response = self.client.post(resumed_url, {**data, "mice": self.mouse.pk})
                self.assertEqual(response.status_code, 302)
                saved = Recording.objects.get(notes="Unfinished notes")
                self.assertEqual(saved.data_paths, ["/path/one", "/path/two"])
                self.assertEqual(saved.recording_date, date(2026, 10, 21))
                self.assertEqual(list(saved.mice.all()), [self.mouse])
                self.assertNotIn(
                    parse_qs(urlsplit(resumed_url).query)["draft"][0],
                    self.client.session["recording_form_drafts"],
                )
                if url == self.add_url:
                    saved.delete()

    def test_incomplete_form_can_select_mice_and_preserve_blank_values(self):
        _, response = self.round_trip(self.edit_url, {"notes": "", "recording_date": ""})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"]["notes"].value(), "")
        self.assertEqual(response.context["form"]["recording_date"].value(), "")
        self.assertFalse(response.context["form"].is_bound)

    def test_drafts_are_isolated_between_tabs_and_recording_forms(self):
        first, _ = self.round_trip(self.add_url, {"notes": "First tab"})
        second, _ = self.round_trip(self.add_url, {"notes": "Second tab"})
        self.assertEqual(self.client.get(first).context["form"]["notes"].value(), "First tab")
        self.assertEqual(self.client.get(second).context["form"]["notes"].value(), "Second tab")
        token = parse_qs(urlsplit(first).query)["draft"][0]
        response = self.client.get(self.edit_url, {"draft": token})
        self.assertEqual(response.context["form"]["notes"].value(), "Saved notes")

    def test_repeated_selection_preserves_the_same_draft(self):
        url = self.add_url
        resumed, _ = self.round_trip(url, {"notes": "First entry"})
        resumed_again, response = self.round_trip(resumed, {
            "notes": "Revised entry", "mice": self.mouse.pk,
        })
        self.assertEqual(response.context["form"]["notes"].value(), "Revised entry")
        self.assertEqual(
            parse_qs(urlsplit(resumed).query)["draft"],
            parse_qs(urlsplit(resumed_again).query)["draft"],
        )
