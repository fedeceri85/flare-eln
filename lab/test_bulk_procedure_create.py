import json
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import BulkMouseUpdateForm, individual_mouse_field_prefix
from .models import Mouse, Procedure, ProcedureType, Protocol
from .test_navigation import Markup
from .views.mice import apply_bulk_mouse_updates


@override_settings(ALLOWED_HOSTS=["testserver"])
class BulkProcedureCreationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="procedure-creation")
        self.client.force_login(self.user)
        self.protocol = Protocol.objects.create(protocol_number=13, licence_reference="CREATE", name="Creation")
        self.restricted = Protocol.objects.create(protocol_number=14, licence_reference="CREATE", name="Restricted", allows_regulated_procedures=False)
        self.mice = [Mouse.objects.create(mouse_id=f"Create-{i}", protocol=self.protocol, notes="Keep notes") for i in range(2)]
        self.kind = ProcedureType.objects.create(name="New procedure type")
        self.urls = [reverse("lab:edit_selected_mice_all"), reverse("lab:edit_selected_mice_licence_protocol", args=[self.protocol.licence_reference, self.protocol.protocol_number])]
        self.url = self.urls[0]

    def draft(self, **extra):
        return {"procedure_type": self.kind.pk, "date": "2026-10-21", "lab_member": "AB", "lab_book_page": "007", **extra}

    def payload(self, drafts=None, **extra):
        return {"apply_bulk_edit": "1", "selected_mouse": [mouse.pk for mouse in self.mice],
                "procedure_drafts": json.dumps([self.draft()] if drafts is None else drafts), **extra}

    def test_pending_editor_is_separate_but_queue_belongs_to_main_form(self):
        for url in self.urls:
            response = self.client.get(url, {"selected_mouse": [mouse.pk for mouse in self.mice]})
            html = Markup(response.content.decode())
            queue = html.find("input", name="procedure_drafts")[0]
            self.assertTrue(queue.within("form", id="primary-form"))
            control = html.find("select", name="new_procedure-procedure_type")[0]
            self.assertTrue(control.within("form", id="litter-procedure-form"))
            self.assertFalse(control.within("form", id="primary-form"))
            self.assertContains(response, "Pending procedures")
            for form in html.find("form"):
                self.assertFalse(form.within("form"))
        self.assertFalse(Procedure.objects.exists())

    def test_procedure_only_save_commits_without_animal_changes_on_both_routes(self):
        for url in self.urls:
            response = self.client.post(url, self.payload())
            self.assertEqual(response.status_code, 302)
        self.assertEqual(Procedure.objects.count(), 4)
        for record in Procedure.objects.all():
            self.assertEqual((record.date, record.lab_member, record.lab_book_page), (date(2026, 10, 21), "AB", "007"))
        for mouse in self.mice:
            mouse.refresh_from_db()
            self.assertEqual((mouse.status, mouse.notes, mouse.protocol_id), ("Alive", "Keep notes", self.protocol.pk))

    def test_empty_save_can_finish_after_recordings_or_existing_procedures_saved(self):
        for url in self.urls:
            self.assertEqual(self.client.post(url, self.payload([])).status_code, 302)
        self.assertFalse(Procedure.objects.exists())

    def test_old_immediate_creation_post_no_longer_commits(self):
        response = self.client.post(self.url, {"create_procedures": "1", "selected_mouse": [mouse.pk for mouse in self.mice],
                                              "new_procedure-procedure_type": self.kind.pk}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Procedure.objects.exists())

    def test_multiple_procedures_and_animal_changes_commit_together(self):
        response = self.client.post(self.url, self.payload([self.draft(), self.draft(lab_member="CD")],
                                                         update_status="on", status="Culled"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Procedure.objects.count(), 4)
        self.assertEqual(set(Procedure.objects.values_list("lab_member", flat=True)), {"AB", "CD"})
        self.assertEqual(set(Mouse.objects.values_list("status", flat=True)), {"Culled"})

    def test_validation_keeps_pending_list_and_never_partially_saves(self):
        for invalid in ({"procedure_type": "999999"}, {"date": "invalid"}, {"lab_member": "X" * 101}):
            drafts = [self.draft(**invalid)]
            response = self.client.post(self.url, self.payload(drafts, update_status="on", status="Culled"))
            self.assertEqual(response.status_code, 200)
            self.assertIn("procedure_drafts", response.context["form"].errors)
            self.assertEqual(json.loads(response.context["form"]["procedure_drafts"].value()), drafts)
            self.assertFalse(Procedure.objects.exists())
            self.assertEqual(set(Mouse.objects.values_list("status", flat=True)), {"Alive"})
        response = self.client.post(self.url, self.payload(update_protocol="on", protocol="999999"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.context["form"]["procedure_drafts"].value()), [self.draft()])
        self.assertFalse(Procedure.objects.exists())

    def test_final_protocol_restrictions_block_all_changes(self):
        response = self.client.post(self.url, self.payload(update_protocol="on", protocol=self.restricted.pk,
                                                         update_status="on", status="Culled"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("procedure_drafts", response.context["form"].errors)
        self.assertFalse(Procedure.objects.exists())
        self.assertEqual(set(Mouse.objects.values_list("status", flat=True)), {"Alive"})
        self.assertEqual(set(Mouse.objects.values_list("protocol_id", flat=True)), {self.protocol.pk})

    def test_protocol_rechecked_at_commit_after_concurrent_change(self):
        form = BulkMouseUpdateForm(self.payload(), selected_mice=self.mice)
        self.assertTrue(form.is_valid(), form.errors)
        Mouse.objects.filter(pk=self.mice[1].pk).update(protocol=self.restricted)
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            apply_bulk_mouse_updates(self.mice, form)
        self.assertFalse(Procedure.objects.exists())

    def test_database_failure_rolls_back_procedures_and_animal_changes(self):
        create = Procedure.objects.create
        calls = []
        def fail_on_second(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise IntegrityError("simulated")
            return create(**kwargs)
        with patch.object(Procedure.objects, "create", side_effect=fail_on_second):
            response = self.client.post(self.url, self.payload(update_status="on", status="Culled"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No changes were saved")
        self.assertFalse(Procedure.objects.exists())
        self.assertEqual(set(Mouse.objects.values_list("status", flat=True)), {"Alive"})
        self.assertEqual(json.loads(response.context["form"]["procedure_drafts"].value()), [self.draft()])

    def test_renamed_animals_receive_pending_procedures(self):
        name = individual_mouse_field_prefix(self.mice[0]) + "_mouse_id"
        response = self.client.post(self.url, self.payload(confirm_mouse_ids="on", **{name: "Renamed animal"}))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(set(Procedure.objects.values_list("mouse_id", flat=True)), {"Renamed animal", self.mice[1].pk})

    def test_optional_details_and_malformed_lists(self):
        response = self.client.post(self.url, self.payload([self.draft(date="", lab_member="", lab_book_page="")]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(all(record.date is None and record.lab_member == "" and record.lab_book_page == "" for record in Procedure.objects.all()))
        for value in ('{}', '[null]', 'invalid', json.dumps([self.draft()] * 101)):
            response = self.client.post(self.url, self.payload([], procedure_drafts=value))
            self.assertEqual(response.status_code, 200)
            self.assertIn("procedure_drafts", response.context["form"].errors)
        self.assertEqual(Procedure.objects.count(), 2)
