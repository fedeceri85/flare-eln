from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse

from .views import mice as views
from .models import Mouse, Procedure, ProcedureType, Protocol


@override_settings(ALLOWED_HOSTS=["testserver"])
class BulkMouseDeleteRouteTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username="delete-routes"))
        self.protocol = Protocol.objects.create(protocol_number=41, licence_reference="DELETE-A", name="A")
        self.other_protocol = Protocol.objects.create(protocol_number=42, licence_reference="DELETE-B", name="B")
        self.mice = [Mouse.objects.create(mouse_id=f"Delete mouse / {i}", protocol=self.protocol) for i in (1, 2)]
        self.outside = Mouse.objects.create(mouse_id="Other protocol", protocol=self.other_protocol)
        kind = ProcedureType.objects.create(name="Delete procedure")
        self.procedure = Procedure.objects.create(mouse=self.mice[0], procedure_type=kind)
        self.global_url = reverse("lab:delete_selected_mice_all")
        self.scoped_url = reverse("lab:delete_selected_mice_licence_protocol", args=["DELETE-A", 41])
        self.legacy_url = reverse("lab:delete_selected_mice", args=[41])
        self.scoped_fallback = reverse("lab:procedure_page_licence_protocol", args=["DELETE-A", 41])
        self.return_url = reverse("lab:mice_page") + "?mouse_id=Delete&page=2"
        self.routes = [(self.global_url, None), (self.scoped_url, self.protocol), (self.legacy_url, self.protocol)]

    def payload(self, **extra):
        return {"selected_mouse": [mouse.pk for mouse in self.mice], "return_url": self.return_url, **extra}

    def test_entry_redirects_to_confirmation_without_deleting_and_preserves_selection(self):
        ids = [self.mice[1].pk, self.mice[0].pk, self.mice[1].pk]
        for url, protocol in self.routes:
            with self.subTest(url=url):
                response = self.client.post(url, self.payload(selected_mouse=ids))
                self.assertEqual(response.status_code, 302)
                destination = urlsplit(response["Location"])
                self.assertEqual(destination.path, self.scoped_url if protocol else self.global_url)
                self.assertEqual(parse_qs(destination.query), {"return_url": [self.return_url], "selected_mouse": ids[:2]})
        self.assertEqual(Mouse.objects.count(), 3)
        self.assertTrue(Procedure.objects.filter(pk=self.procedure.pk).exists())

    def test_confirmation_context_and_counts_are_shared_by_all_routes(self):
        for url, protocol in self.routes:
            response = self.client.get(url, self.payload())
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "lab/delete_selected_mice.html")
            self.assertEqual(response.context["selected_protocol"], protocol)
            self.assertEqual([mouse.pk for mouse in response.context["selected_mice"]], [mouse.pk for mouse in self.mice])
            self.assertEqual(response.context["procedure_count"], 1)
            self.assertEqual(response.context["recording_count"], 0)
            self.assertEqual(response.context["return_url"], self.return_url)
        self.assertEqual(Mouse.objects.count(), 3)

    def test_missing_selection_and_unsafe_return_url_use_scope_fallback(self):
        for url, protocol in self.routes:
            for method in (self.client.get, self.client.post):
                response = method(url, {"return_url": "https://outside.example/"})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], self.scoped_fallback if protocol else reverse("lab:mice_page"))
                self.assertIn("Select at least one animal before deleting.",
                              [str(message) for message in get_messages(response.wsgi_request)])

    def test_missing_mouse_rejects_whole_selection(self):
        for url, protocol in self.routes:
            for method in (self.client.get, self.client.post):
                response = method(url, self.payload(selected_mouse=[self.mice[0].pk, "Missing"], confirm_delete="on"))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], self.return_url)
        self.assertEqual(Mouse.objects.count(), 3)

    def test_scoped_routes_reject_foreign_mice_before_confirmation_or_delete(self):
        for url in (self.scoped_url, self.legacy_url):
            for method in (self.client.get, self.client.post):
                response = method(url, self.payload(selected_mouse=[self.mice[0].pk, self.outside.pk], confirm_delete="on"))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], self.return_url)
                self.assertIn("One or more selected animals could not be found for this protocol.",
                              [str(message) for message in get_messages(response.wsgi_request)])
        self.assertEqual(Mouse.objects.count(), 3)

    def test_false_confirmation_shows_error_and_never_deletes(self):
        for url, protocol in self.routes:
            response = self.client.post(url, self.payload(confirm_delete=""))
            self.assertEqual(response.status_code, 200)
            self.assertIn("confirm_delete", response.context["form"].errors)
        self.assertEqual(Mouse.objects.count(), 3)

    def test_confirmed_delete_uses_locked_selection_on_every_route(self):
        for index, (url, protocol) in enumerate(self.routes):
            with self.subTest(url=url):
                mouse = Mouse.objects.create(mouse_id=f"Delete once {index}", protocol=self.protocol)
                procedure = Procedure.objects.create(mouse=mouse, procedure_type=self.procedure.procedure_type)
                loader = "selected_mice_for_protocol" if protocol else "selected_mice_for_ids"
                with patch.object(views, loader, wraps=getattr(views, loader)) as selected:
                    response = self.client.post(url, self.payload(selected_mouse=[mouse.pk], confirm_delete="on"))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], self.return_url)
                self.assertEqual(selected.call_count, 2)
                self.assertTrue(selected.call_args.kwargs["for_update"])
                self.assertFalse(Mouse.objects.filter(pk=mouse.pk).exists())
                self.assertFalse(Procedure.objects.filter(pk=procedure.pk).exists())
                self.assertTrue(Mouse.objects.filter(pk=self.outside.pk).exists())

    def test_global_delete_accepts_mixed_protocols(self):
        response = self.client.post(self.global_url, self.payload(
            selected_mouse=[self.mice[0].pk, self.outside.pk], confirm_delete="on"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(Mouse.objects.values_list("pk", flat=True)), [self.mice[1].pk])

    def test_protection_on_later_mouse_rolls_back_entire_delete(self):
        original_delete = Mouse.delete

        def delete_or_protect(mouse, *args, **kwargs):
            if mouse.pk == self.mice[1].pk:
                raise ProtectedError("Protected mouse", [mouse])
            return original_delete(mouse, *args, **kwargs)

        for url, protocol in self.routes:
            with patch.object(Mouse, "delete", delete_or_protect):
                response = self.client.post(url, self.payload(confirm_delete="on"))
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response["Location"], self.return_url)
            self.assertEqual(Mouse.objects.count(), 3)
            self.assertTrue(Procedure.objects.filter(pk=self.procedure.pk).exists())
            self.assertIn("Selected animals could not be deleted because another record still protects at least one animal.",
                          [str(message) for message in get_messages(response.wsgi_request)])

    def test_selection_is_rechecked_under_lock_before_deletion(self):
        for url, protocol in self.routes:
            loader = "selected_mice_for_protocol" if protocol else "selected_mice_for_ids"
            with patch.object(views, loader, side_effect=[self.mice, Mouse.DoesNotExist()]):
                response = self.client.post(url, self.payload(confirm_delete="on"))
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response["Location"], self.return_url)
            self.assertTrue(any("could not be found" in str(message)
                                for message in get_messages(response.wsgi_request)))
        self.assertEqual(Mouse.objects.count(), 3)

    def test_protocol_lookup_precedes_selection_validation(self):
        response = self.client.get(reverse("lab:delete_selected_mice_licence_protocol", args=["Unknown", 999]))
        self.assertEqual(response.status_code, 404)
