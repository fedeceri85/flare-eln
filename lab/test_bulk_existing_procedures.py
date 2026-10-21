from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Mouse, Procedure, ProcedureType, Protocol
from .procedure_bulk import SALT


@override_settings(ALLOWED_HOSTS=["testserver"])
class ExistingProcedureBulkTests(TestCase):
    def setUp(self):
        self.protocol = Protocol.objects.create(protocol_number=81, licence_reference="PROC-BULK", name="Trial")
        self.other_protocol = Protocol.objects.create(protocol_number=82, licence_reference="PROC-BULK", name="Other")
        self.mice = [Mouse.objects.create(mouse_id=f"Trial {index}", protocol=self.protocol) for index in range(3)]
        self.outside = Mouse.objects.create(mouse_id="Outside", protocol=self.other_protocol)
        self.types = [ProcedureType.objects.create(name=name) for name in ("First", "Second")]
        self.records = [Procedure.objects.create(mouse=mouse, procedure_type=self.types[index % 2],
                         date=date(2026, 1, index + 1), lab_member="Original", lab_book_page="42", notes="Keep notes")
                        for index, mouse in enumerate([self.mice[0], self.mice[0], self.mice[1], self.outside])]
        self.client.force_login(get_user_model().objects.create_user(username="procedure-editor"))
        self.urls = [reverse("lab:edit_selected_mice_all"), reverse("lab:edit_selected_mice_licence_protocol", kwargs={
            "licence_reference": self.protocol.licence_reference, "protocol_number": self.protocol.protocol_number})]
        self.url = self.urls[0]

    def data(self, **changes):
        data = {"selected_mouse": [mouse.pk for mouse in self.mice],
                "existing_procedure_action": "preview",
                "existing_procedures-selected_procedures": [self.records[0].pk, self.records[2].pk],
                "existing_procedures-update_lab_member": "on", "existing_procedures-lab_member": "Updated"}
        data.update(changes)
        return data

    def post(self, data):
        return self.client.post(self.url, data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")

    def preview(self, data):
        response = self.post(data)
        self.assertEqual(response.status_code, 200, response.content)
        token = response.context["existing_procedure_form"]["snapshot"].value()
        self.assertTrue(token)
        return token

    def save_data(self, data):
        token = self.preview(data)
        return {**data, "existing_procedure_action": "save", "existing_procedures-snapshot": token}

    def test_preview_and_save_exact_records_on_both_routes(self):
        for url in self.urls:
            with self.subTest(url=url):
                self.url = url
                Procedure.objects.update(lab_member="Original")
                data = self.save_data(self.data())
                self.assertEqual(list(Procedure.objects.values_list("lab_member", flat=True)), ["Original"] * 4)
                # Main mouse/new-procedure fields must have no effect on this action.
                data.update({"apply_bulk_edit": "1", "update_notes": "on", "notes": "Wrong",
                             "procedure_type": self.types[1].pk})
                self.assertEqual(self.post(data).status_code, 200)
                self.assertEqual(Procedure.objects.count(), 4)
                for index, record in enumerate(self.records):
                    record.refresh_from_db()
                    self.assertEqual(record.lab_member, "Updated" if index in (0, 2) else "Original")
                    self.assertEqual(record.notes, "Keep notes")
                    self.assertEqual(record.lab_book_page, "42")
                    self.assertEqual(record.mouse_id, ([self.mice[0], self.mice[0], self.mice[1], self.outside][index]).pk)
                self.assertFalse(Mouse.objects.filter(notes="Wrong").exists())

    def test_checked_blank_fields_clear_and_unchecked_values_ignored(self):
        data = self.data(**{"existing_procedures-update_date": "on", "existing_procedures-date": "",
                            "existing_procedures-update_lab_book_page": "on", "existing_procedures-lab_book_page": "",
                            "existing_procedures-lab_member": "", "existing_procedures-procedure_type": "invalid"})
        self.assertEqual(self.post(self.save_data(data)).status_code, 200)
        self.records[0].refresh_from_db()
        self.assertIsNone(self.records[0].date)
        self.assertEqual(self.records[0].lab_member, "")
        self.assertEqual(self.records[0].lab_book_page, "")
        self.assertEqual(self.records[0].procedure_type_id, self.types[0].pk)

    def test_checked_type_requires_valid_choice(self):
        for value in ("", "999999", "nonsense"):
            data = self.data(**{"existing_procedures-update_procedure_type": "on", "existing_procedures-procedure_type": value})
            self.assertEqual(self.post(data).status_code, 400)
        data = self.data(**{"existing_procedures-update_procedure_type": "on", "existing_procedures-procedure_type": self.types[1].pk})
        self.assertEqual(self.post(self.save_data(data)).status_code, 200)
        self.records[0].refresh_from_db()
        self.assertEqual(self.records[0].procedure_type_id, self.types[1].pk)

    def test_out_of_selection_ids_and_empty_selection_rejected(self):
        for url in self.urls:
            self.url = url
            for ids in ([self.records[3].pk], [999999], []):
                self.assertEqual(self.post(self.data(**{"existing_procedures-selected_procedures": ids})).status_code, 400)

    def test_stale_deleted_and_reassigned_records_do_not_partially_save(self):
        for change in ("date", "delete", "reassign"):
            with self.subTest(change=change):
                data = self.save_data(self.data())
                record = self.records[0]
                if change == "date":
                    Procedure.objects.filter(pk=record.pk).update(date=date(2025, 1, 1))
                elif change == "delete":
                    Procedure.objects.filter(pk=record.pk).delete()
                else:
                    Procedure.objects.filter(pk=record.pk).update(mouse=self.outside)
                response = self.post(data)
                self.assertIn(response.status_code, (400, 409))
                self.records[2].refresh_from_db()
                self.assertEqual(self.records[2].lab_member, "Original")
                # Restore the fixture for the next scenario.
                record.date = date(2026, 1, 1)
                record.mouse = self.mice[0]
                record.save()

    def test_changed_controls_scope_and_tampered_expired_snapshot(self):
        original = self.save_data(self.data())
        variants = [
            {"existing_procedures-lab_member": "Different"},
            {"existing_procedures-selected_procedures": [self.records[0].pk]},
            {"selected_mouse": [self.mice[0].pk, self.mice[1].pk]},
            {"existing_procedures-snapshot": "tampered"},
        ]
        for changes in variants:
            self.assertEqual(self.post({**original, **changes}).status_code, 409)
        with patch("django.core.signing.time.time", return_value=1):
            expired = signing.dumps(signing.loads(original["existing_procedures-snapshot"], salt=SALT), salt=SALT)
        self.assertEqual(self.post({**original, "existing_procedures-snapshot": expired}).status_code, 409)
        self.assertFalse(Procedure.objects.filter(lab_member="Updated").exists())

    def test_database_failure_rolls_back_and_stays_inline(self):
        data = self.save_data(self.data())
        from django.db.models.query import QuerySet
        original = QuerySet.update
        def fail_after_update(queryset, **kwargs):
            result = original(queryset, **kwargs)
            if queryset.model is Procedure:
                raise IntegrityError("simulated")
            return result
        with patch.object(QuerySet, "update", fail_after_update):
            response = self.post(data)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(Procedure.objects.filter(lab_member="Updated").exists())

    def test_readonly_without_javascript_and_separate_prefixed_form(self):
        response = self.client.get(self.url, {"selected_mouse": [mouse.pk for mouse in self.mice]})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("read-only without JavaScript", html)
        self.assertIn("Add new procedure", html)
        existing_section = '<section class="bulk-section procedure-editor" id="mouse-panel-existing-procedures"'
        self.assertIn(existing_section, html)
        self.assertNotIn('data-existing-procedure-disclosure', html)
        self.assertLess(html.index("Add new procedure"), html.index(existing_section))
        self.assertLess(html.index(existing_section), html.index("data-existing-procedure-editor"))
        self.assertIn('form="primary-form" formaction=', html)
        self.assertEqual(html.count('form="primary-form"'), 2)
        self.assertEqual(html.count('>Cancel</a>'), 1)
        self.assertEqual(html.count('>Delete selected</button>'), 1)
        self.assertIn('page-action-bar bulk-mouse-action-bar', html)
        self.assertEqual(html.count('data-sticky-selected-mice'), 1)
        self.assertLess(html.index('data-sticky-selected-mice'), html.index('id="primary-form"'))
        self.assertLess(html.index(self.mice[0].mouse_id), html.index('id="primary-form"'))
        self.assertLess(html.index('>Delete selected</button>'), html.index('id="primary-form"'))
        self.assertGreater(html.index('data-existing-procedure-editor'), html.index('</form>', html.index('id="primary-form"')))
        self.assertIn('name="existing_procedures-update_date"', html)
        rows = response.context["existing_procedure_rows"]
        self.assertEqual([row["record"].pk for row in rows], [self.records[1].pk, self.records[0].pk, self.records[2].pk])
        self.assertEqual(self.client.post(self.url, self.data()).status_code, 400)
        empty = self.client.get(self.url, {"selected_mouse": self.mice[2].pk})
        self.assertContains(empty, "No existing procedures belong")

    def test_no_updates_selected_is_invalid(self):
        data = self.data()
        del data["existing_procedures-update_lab_member"]
        self.assertEqual(self.post(data).status_code, 400)

    def test_existing_edit_allowed_when_creation_is_disabled(self):
        self.protocol.allows_regulated_procedures = False
        self.protocol.save()
        for url in self.urls:
            self.url = url
            self.assertEqual(self.post(self.save_data(self.data())).status_code, 200)
        self.assertEqual(Procedure.objects.count(), 4)

    def test_snapshot_cannot_be_used_in_another_editor_scope_or_without_preview(self):
        data = self.save_data(self.data())
        self.url = self.urls[1]
        self.assertEqual(self.post(data).status_code, 409)
        self.assertEqual(self.post({**data, "existing_procedures-snapshot": ""}).status_code, 409)
        self.assertFalse(Procedure.objects.filter(lab_member="Updated").exists())

    def test_unchecked_invalid_fields_are_ignored_and_preview_shows_before_after(self):
        data = self.data(**{"existing_procedures-date": "invalid", "existing_procedures-lab_book_page": "x" * 100})
        response = self.post(data)
        self.assertEqual(response.status_code, 200)
        preview = response.context["procedure_preview"]
        self.assertEqual([row["record"].pk for row in preview], [self.records[0].pk, self.records[2].pk])
        self.assertEqual(preview[0]["changes"], [{"label": "Lab member", "before": "Original", "after": "Updated"}])
        token = response.context["existing_procedure_form"]["snapshot"].value()
        self.assertEqual(self.post({**data, "existing_procedure_action": "save", "existing_procedures-snapshot": token}).status_code, 200)
        self.records[0].refresh_from_db()
        self.assertEqual(self.records[0].date, date(2026, 1, 1))
        self.assertEqual(self.records[0].lab_book_page, "42")

    def deletion_data(self):
        response = self.client.get(self.url, {"selected_mouse": [mouse.pk for mouse in self.mice]})
        data = {"selected_mouse": [mouse.pk for mouse in self.mice],
                "existing_procedure_action": "delete", "existing_procedures-confirm_delete": "1",
                "existing_procedures-selected_procedures": [self.records[0].pk, self.records[2].pk]}
        for row in response.context["existing_procedure_rows"]:
            data[f"existing_procedures-delete_snapshot_{row['record'].pk}"] = row["delete_snapshot"]
        return data

    def test_confirmed_deletion_removes_exact_records_on_both_routes(self):
        for url in self.urls:
            self.url = url
            data = self.deletion_data()
            self.assertEqual(Procedure.objects.count(), 4)
            data.update({"apply_bulk_edit": "1", "procedure_type": self.types[0].pk,
                         "notes_action": "replace", "notes": "Wrong", "update_status": "on", "status": "Wrong"})
            response = self.post(data)
            self.assertEqual(response.status_code, 200)
            self.assertIn("Deleted 2 procedure records", response.json()["html"])
            self.assertNotIn("Preview deletion", response.json()["html"])
            self.assertEqual(set(Procedure.objects.values_list("pk", flat=True)), {self.records[1].pk, self.records[3].pk})
            self.assertEqual(Mouse.objects.count(), 4)
            self.assertFalse(Mouse.objects.filter(notes="Wrong").exists())
            self.assertFalse(Mouse.objects.filter(status="Wrong").exists())
            for record in (self.records[0], self.records[2]):
                record.save(force_insert=True)

    def test_deletion_requires_confirmation_and_valid_table_tokens(self):
        data = self.deletion_data()
        self.assertEqual(self.post({**data, "existing_procedures-confirm_delete": ""}).status_code, 400)
        key = f"existing_procedures-delete_snapshot_{self.records[0].pk}"
        self.assertEqual(self.post({**data, key: ""}).status_code, 409)
        self.assertEqual(self.post({**data, key: "tampered"}).status_code, 409)
        update_token = self.preview(self.data())
        self.assertEqual(self.post({**data, key: update_token}).status_code, 409)
        self.assertEqual(self.post({**data, "existing_procedures-selected_procedures": [self.records[3].pk]}).status_code, 400)
        self.assertEqual(Procedure.objects.count(), 4)

    def test_delete_table_token_cannot_authorize_updates_or_different_scope(self):
        data = self.deletion_data()
        key = f"existing_procedures-delete_snapshot_{self.records[0].pk}"
        response = self.post({**self.data(), "existing_procedure_action": "save",
                              "existing_procedures-snapshot": data[key]})
        self.assertEqual(response.status_code, 409)
        self.url = self.urls[1]
        self.assertEqual(self.post(data).status_code, 409)
        self.assertFalse(Procedure.objects.filter(lab_member="Updated").exists())
        self.assertEqual(Procedure.objects.count(), 4)

    def test_deletion_ignores_unrelated_invalid_edit_values(self):
        response = self.post({**self.deletion_data(), "existing_procedures-update_date": "on", "existing_procedures-date": "invalid",
                              "existing_procedures-update_procedure_type": "on", "existing_procedures-procedure_type": ""})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Procedure.objects.count(), 2)

    def test_changed_notes_missing_and_reassigned_records_reject_deletion_without_partial_changes(self):
        for change in ("notes", "missing", "reassigned"):
            with self.subTest(change=change):
                data = self.deletion_data()
                record = self.records[0]
                if change == "notes":
                    Procedure.objects.filter(pk=record.pk).update(notes="New notes")
                elif change == "missing":
                    Procedure.objects.filter(pk=record.pk).delete()
                else:
                    Procedure.objects.filter(pk=record.pk).update(mouse=self.outside)
                self.assertIn(self.post(data).status_code, (400, 409))
                self.assertTrue(Procedure.objects.filter(pk=self.records[2].pk).exists())
                record.save()

    def test_deletion_expired_token_rejected_and_database_failure_rolls_back(self):
        data = self.deletion_data()
        key = f"existing_procedures-delete_snapshot_{self.records[0].pk}"
        with patch("django.core.signing.time.time", return_value=1):
            expired = signing.dumps(signing.loads(data[key], salt=SALT), salt=SALT)
        self.assertEqual(self.post({**data, key: expired}).status_code, 409)
        from django.db.models.query import QuerySet
        original = QuerySet.delete
        def fail_after_delete(queryset):
            result = original(queryset)
            if queryset.model is Procedure:
                raise IntegrityError("simulated")
            return result
        with patch.object(QuerySet, "delete", fail_after_delete):
            self.assertEqual(self.post(data).status_code, 409)
        self.assertEqual(Procedure.objects.count(), 4)

    def test_single_procedure_editor_has_separate_delete_action_and_confirmation(self):
        from .test_navigation import Markup
        record = self.records[0]
        response = self.client.get(reverse("lab:edit_mouse_procedure", args=[record.mouse_id, record.pk]))
        self.assertEqual(response.status_code, 200)
        html = Markup(response.content.decode())
        button = html.find("button", form="delete-procedure-form")
        self.assertEqual(len(button), 1)
        self.assertIn("Delete procedure", button[0].text)
        form = html.find("form", id="delete-procedure-form")[0]
        self.assertFalse(form.within("form", id="primary-form"))
        self.assertIn("confirm(", form.attrs["onsubmit"])
        response = self.client.post(form.attrs["action"], {"return_url": self.url})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Procedure.objects.filter(pk=record.pk).exists())
        self.assertEqual(Procedure.objects.count(), 3)
        self.assertTrue(Mouse.objects.filter(pk=record.mouse_id).exists())
