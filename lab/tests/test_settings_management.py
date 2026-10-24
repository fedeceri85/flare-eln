from datetime import date

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from ..models import GenotypeCategory, Experiment, Project, Recording, RecordingField, RecordingType
from ..test_navigation import Markup


@override_settings(ALLOWED_HOSTS=["testserver"])
class SettingsManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_user(username="settings-admin", is_staff=True)
        cls.user = get_user_model().objects.create_user(username="settings-user")
        cls.kind = RecordingType.objects.create(name="Settings recording", slug="settings-recording")
        cls.field = RecordingField.objects.create(recording_type=cls.kind, key="metric", label="Metric", data_type="integer")
        cls.category = GenotypeCategory.objects.create(code="settings-genotype", label="Settings genotype", position=50)

    def setUp(self):
        self.client.force_login(self.admin)

    def url(self, route, *args):
        return reverse(f"lab:{route}", args=args)

    def admin_routes(self):
        return [
            self.url("genotype_categories"), self.url("add_genotype_category"), self.url("edit_genotype_category", self.category.pk),
            self.url("recording_types"), self.url("add_recording_type"), self.url("edit_recording_type", self.kind.pk),
            self.url("delete_recording_type", self.kind.pk), self.url("add_recording_field", self.kind.pk),
            self.url("edit_recording_field", self.kind.pk, self.field.pk),
        ]

    def field_data(self, **overrides):
        data = {
            "key": "metric", "label": "Metric", "data_type": "integer", "choices": "[]", "default_value": "0",
            "active": "on", "display_in_table": "on", "filterable": "on", "order": "2",
        }
        data.update(overrides)
        return data

    def test_settings_cards_are_only_visible_to_administrators(self):
        response = self.client.get(self.url("home"))
        self.assertNotContains(response, "Admin Console")
        response = self.client.get(self.url("settings_page"))
        for route in ("import_templates", "genotype_categories", "recording_types"):
            self.assertContains(response, self.url(route))
        self.client.force_login(self.user)
        response = self.client.get(self.url("settings_page"))
        self.assertContains(response, self.url("import_templates"))
        self.assertNotContains(response, self.url("genotype_categories"))
        self.assertNotContains(response, self.url("recording_types"))

    def test_all_management_routes_reject_non_admin_get_and_post(self):
        self.client.force_login(self.user)
        for url in self.admin_routes():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url, {"label": "Unauthorized"}).status_code, 403)
        self.kind.refresh_from_db()
        self.assertEqual(self.kind.name, "Settings recording")
        self.category.refresh_from_db()
        self.assertEqual(self.category.label, "Settings genotype")
        self.client.logout()
        for url in self.admin_routes():
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_admin_pages_render_with_settings_navigation_and_post_requires_csrf(self):
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.admin)
        for url in self.admin_routes():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                html = Markup(response.content.decode())
                self.assertEqual([node.text for node in html.find("a", **{"aria-current": "true"})], ["Settings"])
                self.assertEqual(protected.post(url, {}).status_code, 403)

    def test_create_category_and_retire_without_changing_stored_code(self):
        response = self.client.post(self.url("add_genotype_category"), {
            "label": "New category", "code": "new-category", "position": "3", "active": "on",
        })
        self.assertRedirects(response, self.url("genotype_categories"))
        category = GenotypeCategory.objects.get(code="new-category")
        response = self.client.post(self.url("edit_genotype_category", category.pk), {
            "label": "Renamed category", "code": "tampered-code", "position": "4",
        })
        self.assertRedirects(response, self.url("genotype_categories"))
        category.refresh_from_db()
        self.assertEqual((category.code, category.label, category.position, category.active), ("new-category", "Renamed category", 4, False))

    def test_unknown_category_cannot_be_retired_and_duplicate_codes_are_rejected(self):
        unknown = GenotypeCategory.objects.get(code="unknown")
        response = self.client.post(self.url("edit_genotype_category", unknown.pk), {
            "label": unknown.label, "code": "unknown", "position": unknown.position,
        })
        self.assertContains(response, "Unknown must remain available")
        unknown.refresh_from_db()
        self.assertTrue(unknown.active)
        response = self.client.post(self.url("add_genotype_category"), {
            "label": "Different label", "code": self.category.code, "position": "0", "active": "on",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertFalse(GenotypeCategory.objects.filter(label="Different label").exists())

    def test_create_and_edit_recording_type_with_fields(self):
        response = self.client.post(self.url("add_recording_type"), {
            "name": "New kind", "plural_name": "New kinds", "slug": "new-kind", "description": "Description", "active": "on",
        })
        kind = RecordingType.objects.get(slug="new-kind")
        self.assertRedirects(response, self.url("edit_recording_type", kind.pk))
        response = self.client.post(self.url("add_recording_field", kind.pk), self.field_data(required="on"))
        self.assertRedirects(response, self.url("edit_recording_type", kind.pk))
        field = kind.fields.get()
        self.assertEqual((field.key, field.default_value, field.required, field.filterable), ("metric", 0, True, True))
        self.assertContains(self.client.get(self.url("edit_recording_type", kind.pk)), self.url("edit_recording_field", kind.pk, field.pk))
        response = self.client.post(self.url("edit_recording_type", kind.pk), {
            "name": "Retired kind", "plural_name": "Retired kinds", "slug": "new-kind", "description": "Updated",
        })
        self.assertEqual(response.status_code, 302)
        kind.refresh_from_db()
        self.assertEqual((kind.name, kind.active), ("Retired kind", False))

    def test_field_validation_rejects_duplicate_key_and_invalid_default(self):
        response = self.client.post(self.url("add_recording_field", self.kind.pk), self.field_data())
        self.assertContains(response, "already used by another field")
        response = self.client.post(self.url("add_recording_field", self.kind.pk), self.field_data(key="other", default_value='"not a number"'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertFalse(self.kind.fields.filter(key="other").exists())
        response = self.client.post(self.url("add_recording_type"), {"name": "Duplicate", "slug": self.kind.slug, "active": "on"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)

    def linked_recording(self):
        project = Project.objects.create(name="Settings test")
        experiment = Experiment.objects.create(project=project, name="Linked experiment", recording_type=self.kind)
        return Recording.objects.create(experiment=experiment, recording_type=self.kind, owner=self.admin,
                                        recording_date=date(2026, 10, 24), values={"metric": 7})

    def test_historical_keys_are_protected_and_retiring_fields_keeps_values(self):
        recording = self.linked_recording()
        response = self.client.post(self.url("edit_recording_field", self.kind.pk, self.field.pk), self.field_data(key="changed"))
        self.assertContains(response, "Field keys cannot be changed")
        response = self.client.post(self.url("edit_recording_field", self.kind.pk, self.field.pk), self.field_data(active=""))
        self.assertEqual(response.status_code, 302)
        self.field.refresh_from_db()
        self.assertFalse(self.field.active)
        self.assertEqual(self.field.key, "metric")
        recording.refresh_from_db()
        self.assertEqual(recording.values, {"metric": 7})

    def test_field_must_belong_to_type_in_url(self):
        other = RecordingType.objects.create(name="Other", slug="other-settings-type")
        self.assertEqual(self.client.get(self.url("edit_recording_field", other.pk, self.field.pk)).status_code, 404)
        self.assertEqual(self.client.post(self.url("edit_recording_field", other.pk, self.field.pk), self.field_data()).status_code, 404)

    def test_type_deletion_requires_post_and_linked_types_are_protected(self):
        delete_url = self.url("delete_recording_type", self.kind.pk)
        self.assertContains(self.client.get(delete_url), "Delete recording type")
        self.assertTrue(RecordingType.objects.filter(pk=self.kind.pk).exists())
        recording = self.linked_recording()
        response = self.client.post(delete_url)
        self.assertContains(response, "linked to experiments or recordings")
        self.assertTrue(Recording.objects.filter(pk=recording.pk).exists())
        unused = RecordingType.objects.create(name="Unused", slug="unused-settings-type")
        response = self.client.post(self.url("delete_recording_type", unused.pk))
        self.assertRedirects(response, self.url("recording_types"))
        self.assertFalse(RecordingType.objects.filter(pk=unused.pk).exists())
