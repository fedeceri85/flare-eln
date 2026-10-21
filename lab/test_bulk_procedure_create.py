from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from .models import Mouse, Procedure, ProcedureType, Protocol
from .procedure_bulk import handle_create_procedures
from .test_navigation import Markup


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

    def payload(self, **extra):
        return {"create_procedures": "1", "selected_mouse": [mouse.pk for mouse in self.mice],
                "new_procedure-procedure_type": self.kind.pk, "new_procedure-date": "2026-10-21",
                "new_procedure-lab_member": "AB", "new_procedure-lab_book_page": "007", **extra}

    def post(self, data):
        return self.client.post(self.url, data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")

    def test_independent_form_with_inline_add_button_on_both_routes(self):
        for url in self.urls:
            response = self.client.get(url, {"selected_mouse": [mouse.pk for mouse in self.mice]})
            html = Markup(response.content.decode())
            control = html.find("select", name="new_procedure-procedure_type")[0]
            self.assertTrue(control.within("form", id="new-procedure-form"))
            self.assertFalse(control.within("form", id="primary-form"))
            add = next(node for node in html.find("button") if node.text.strip() == "Add procedure")
            self.assertTrue(add.within("fieldset", **{"data-new-procedure-inputs": None}))
            self.assertTrue(add.within("form", id="new-procedure-form"))
            for form in html.find("form"):
                self.assertFalse(form.within("form"))

    def test_creation_updates_only_procedures_and_refreshes_existing_list_on_both_routes(self):
        original = Procedure.objects.create(mouse=self.mice[0], procedure_type=self.kind, notes="Original procedure")
        for url in self.urls:
            self.url = url
            before = Procedure.objects.count()
            response = self.post(self.payload(apply_bulk_edit="1", update_status="on", status="Culled",
                                               notes_action="replace", notes="Wrong", update_protocol="on", protocol=self.restricted.pk))
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(Procedure.objects.count(), before + 2)
            self.assertIn("Added 2 procedure records", response.json()["creation_html"])
            self.assertIn("New procedure type", response.json()["existing_html"])
            self.assertIn("existing_procedures-delete_snapshot_", response.json()["existing_html"])
            created = Procedure.objects.filter(lab_member="AB")
            self.assertTrue(all(record.date == date(2026, 10, 21) and record.lab_book_page == "007" for record in created))
            for mouse in self.mice:
                mouse.refresh_from_db()
                self.assertEqual((mouse.status, mouse.notes, mouse.protocol_id), ("Alive", "Keep notes", self.protocol.pk))
            original.refresh_from_db()
            self.assertEqual(original.notes, "Original procedure")

    def test_main_save_ignores_new_and_legacy_procedure_values(self):
        for url in self.urls:
            data = self.payload(apply_bulk_edit="1", update_status="on", status="Culled",
                                procedure_type=self.kind.pk, procedure_date="invalid",
                                **{"new_procedure-date": "invalid"})
            del data["create_procedures"]
            response = self.client.post(url, data)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(Procedure.objects.count(), 0)
            self.assertTrue(all(Mouse.objects.get(pk=mouse.pk).status == "Culled" for mouse in self.mice))

    def test_validation_is_inline_and_does_not_save(self):
        for extra in ({"new_procedure-procedure_type": ""}, {"new_procedure-procedure_type": "99999"},
                      {"new_procedure-date": "invalid"}, {"new_procedure-lab_member": "X" * 101}):
            response = self.post(self.payload(**extra))
            self.assertEqual(response.status_code, 400)
            self.assertIn("error-summary", response.json()["creation_html"])
            self.assertNotIn("existing_html", response.json())
        self.assertEqual(Procedure.objects.count(), 0)

    def test_optional_values_can_be_blank(self):
        response = self.post(self.payload(**{"new_procedure-date": "", "new_procedure-lab_member": "", "new_procedure-lab_book_page": ""}))
        self.assertEqual(response.status_code, 200)
        for record in Procedure.objects.all():
            self.assertIsNone(record.date)
            self.assertEqual((record.lab_member, record.lab_book_page), ("", ""))

    def test_creation_protocol_restrictions_are_checked_against_persisted_mice(self):
        Mouse.objects.filter(pk=self.mice[1].pk).update(protocol=self.restricted)
        response = self.post(self.payload(update_protocol="on", protocol=self.protocol.pk))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Procedure.objects.count(), 0)
        self.assertIn("creation is disabled", response.json()["creation_html"])

    def test_missing_or_wrong_protocol_mouse_selection_cannot_create(self):
        response = self.post(self.payload(selected_mouse=["missing"]))
        self.assertEqual(response.status_code, 302)
        outside = Mouse.objects.create(mouse_id="Outside", protocol=self.restricted)
        self.url = self.urls[1]
        response = self.post(self.payload(selected_mouse=[self.mice[0].pk, outside.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Procedure.objects.count(), 0)

    def test_scope_is_rechecked_after_initial_mouse_lookup(self):
        Mouse.objects.filter(pk=self.mice[1].pk).update(protocol=self.restricted)
        request = RequestFactory().post(self.urls[1], self.payload(), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        request.user = self.user
        response = handle_create_procedures(request, self.mice, self.protocol)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Procedure.objects.count(), 0)

    def test_database_error_rolls_back_all_created_records(self):
        create = Procedure.objects.create
        calls = []
        def fail_on_second(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise IntegrityError("simulated")
            return create(**kwargs)
        with patch.object(Procedure.objects, "create", side_effect=fail_on_second):
            response = self.post(self.payload())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Procedure.objects.count(), 0)
        self.assertIn("No procedures were added", response.json()["creation_html"])

    def test_native_post_cannot_add_and_lose_unsaved_mouse_inputs(self):
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Procedure.objects.count(), 0)
