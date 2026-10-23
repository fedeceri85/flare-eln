from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Mouse, Procedure, ProcedureType, Protocol


@override_settings(ALLOWED_HOSTS=["testserver"])
class BulkMouseEditorRouteTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username="bulk-routes"))
        self.protocol = Protocol.objects.create(protocol_number=41, licence_reference="ROUTES-A", name="A")
        self.other_protocol = Protocol.objects.create(protocol_number=42, licence_reference="ROUTES-B", name="B",
                                                       allows_regulated_procedures=False)
        self.mice = [Mouse.objects.create(mouse_id=f"Route mouse / {i}", protocol=self.protocol, status="Alive") for i in (1, 2)]
        self.outside = Mouse.objects.create(mouse_id="Other protocol", protocol=self.other_protocol, status="Alive")
        self.kind = ProcedureType.objects.create(name="Route procedure")
        self.global_url = reverse("lab:edit_selected_mice_all")
        self.scoped_url = reverse("lab:edit_selected_mice_licence_protocol", args=[self.protocol.licence_reference, 41])
        self.legacy_url = reverse("lab:edit_selected_mice", args=[41])
        self.scoped_fallback = reverse("lab:procedure_page_licence_protocol", args=[self.protocol.licence_reference, 41])
        self.return_url = reverse("lab:mice_page") + "?mouse_id=Route&page=2"
        self.routes = [(self.global_url, None), (self.scoped_url, self.protocol), (self.legacy_url, self.protocol)]

    def fallback(self, protocol):
        return self.scoped_fallback if protocol is not None else reverse("lab:mice_page")

    def test_selection_entry_redirects_to_the_correct_editor_and_preserves_order(self):
        ids = [self.mice[1].pk, self.mice[0].pk, self.mice[1].pk]
        for url, protocol in self.routes:
            with self.subTest(url=url):
                response = self.client.post(url, {"selected_mouse": ids, "return_url": self.return_url})
                self.assertEqual(response.status_code, 302)
                destination = urlsplit(response["Location"])
                self.assertEqual(destination.path, self.scoped_url if protocol is not None else self.global_url)
                self.assertEqual(parse_qs(destination.query), {"return_url": [self.return_url], "selected_mouse": ids[:2]})

    def test_single_mouse_entry_uses_the_same_editor(self):
        for url, protocol in self.routes:
            response = self.client.post(url, {"selected_mouse": [self.mice[0].pk], "return_url": self.return_url})
            self.assertEqual(response.status_code, 302)
            destination = urlsplit(response["Location"])
            self.assertEqual(destination.path, self.scoped_url if protocol is not None else self.global_url)
            self.assertEqual(parse_qs(destination.query), {"return_url": [self.return_url], "selected_mouse": [self.mice[0].pk]})

    def test_missing_selection_and_unsafe_return_urls_use_scope_fallback(self):
        for url, protocol in self.routes:
            for method in (self.client.get, self.client.post):
                response = method(url, {"return_url": "https://outside.example/"})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], self.fallback(protocol))
                self.assertIn("Select at least one animal before editing.", [str(message) for message in get_messages(response.wsgi_request)])

    def test_missing_mouse_rejects_entire_selection_on_every_route(self):
        for url, protocol in self.routes:
            for method in (self.client.get, self.client.post):
                response = method(url, {"selected_mouse": [self.mice[0].pk, "Missing mouse"],
                                       "apply_bulk_edit": "1", "update_status": "on", "status": "Changed"})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], self.fallback(protocol))
        self.assertFalse(Mouse.objects.exclude(status="Alive").exists())

    def test_scoped_selection_rejects_foreign_mice_before_any_action(self):
        actions = [{}, {"apply_bulk_edit": "1", "update_status": "on", "status": "Changed"},
                   {"create_procedures": "1", "new_procedure-procedure_type": self.kind.pk},
                   {"existing_procedure_action": "preview"}, {"existing_procedure_action": "delete"}]
        for url in (self.scoped_url, self.legacy_url):
            for action in actions:
                response = self.client.post(url, {"selected_mouse": [self.mice[0].pk, self.outside.pk], **action},
                                            HTTP_X_REQUESTED_WITH="XMLHttpRequest")
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], self.scoped_fallback)
                self.assertIn("One or more selected animals could not be found for this protocol.",
                              [str(message) for message in get_messages(response.wsgi_request)])
        self.assertFalse(Procedure.objects.exists())
        self.assertFalse(Mouse.objects.exclude(status="Alive").exists())

    def test_editor_context_preserves_scoped_links_and_return_selection(self):
        ids = [mouse.pk for mouse in self.mice]
        for url, protocol in self.routes:
            response = self.client.get(url, {"selected_mouse": ids, "return_url": self.return_url})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["selected_protocol"], protocol)
            self.assertEqual([mouse.pk for mouse in response.context["selected_mice"]], ids)
            delete_url = (reverse("lab:delete_selected_mice_licence_protocol", args=[self.protocol.licence_reference, 41])
                          if protocol is not None else reverse("lab:delete_selected_mice_all"))
            self.assertEqual(response.context["delete_selected_url"], delete_url)
            procedure_return = urlsplit(response.context["new_procedure_return_url"])
            self.assertEqual(procedure_return.path, self.scoped_url if protocol is not None else self.global_url)
            self.assertEqual(parse_qs(procedure_return.query), {"return_url": [self.return_url], "selected_mouse": ids})

    def test_global_editor_can_save_mixed_protocol_mice(self):
        response = self.client.post(self.global_url, {
            "selected_mouse": [self.mice[0].pk, self.outside.pk], "apply_bulk_edit": "1",
            "update_status": "on", "status": "Culled", "return_url": self.return_url,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.return_url)
        self.assertEqual(set(Mouse.objects.filter(status="Culled").values_list("pk", flat=True)),
                         {self.mice[0].pk, self.outside.pk})
        self.assertFalse(Procedure.objects.exists())

    def test_ajax_preview_never_saves_mouse_changes(self):
        for url, protocol in self.routes:
            response = self.client.post(url, {
                "selected_mouse": [mouse.pk for mouse in self.mice], "apply_bulk_edit": "1",
                "preview_individual_mice": "1", "update_status": "on", "status": "Wrong",
            }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
            self.assertEqual(response.status_code, 200)
            self.assertIn("html", response.json())
        self.assertFalse(Mouse.objects.exclude(status="Alive").exists())

    def test_protocol_lookup_still_precedes_selection_validation(self):
        response = self.client.get(reverse("lab:edit_selected_mice_licence_protocol", args=["Not a licence", 999]))
        self.assertEqual(response.status_code, 404)
