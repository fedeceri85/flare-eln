import json

from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase

from .admin import GenotypeCategoryAdmin
from .forms import BulkMouseUpdateForm, MouseEditForm, genotype_field_name, sync_mouse_structured_genetics
from .models import GenotypeCategory, Mouse, MouseGenotype, MouseLine, Protocol, Species, genotype_category_choices, resolve_crossing
from .views import crossing_genotype_fields


class GenotypeCategoryTests(TestCase):
    def setUp(self):
        self.species = Species.objects.create(name="Category test species")
        self.line = MouseLine.objects.create(species=self.species, name="Test line")
        self.crossing, _ = resolve_crossing(self.species, [self.line])
        protocol = Protocol.objects.create(protocol_number=1, licence_reference="CAT", name="Category test")
        self.mouse = Mouse.objects.create(mouse_id="CATEGORY-1", protocol=protocol)
        self.field = genotype_field_name(self.line)

    def choices_response(self):
        return json.loads(crossing_genotype_fields(RequestFactory().get(
            "/", {"crossing": self.crossing.pk, "mouse": self.mouse.pk},
        )).content)

    def test_seeded_categories(self):
        self.assertEqual([label for _, label in genotype_category_choices()], [
            "Unknown", "WT", "Het", "Hom", "Cre", "Flx/Null",
            "Null/Null", "Flx/Y", "Null/Y", "Null/WT", "WT/Y",
        ])

    def test_custom_category_in_forms_endpoint_and_saved_results(self):
        GenotypeCategory.objects.create(code="custom", label="Custom result", position=50)
        sync_mouse_structured_genetics(self.mouse, self.crossing, {self.line.pk: "custom"})
        form = MouseEditForm(instance=self.mouse)
        form.fields[self.field].clean("custom")
        bulk = BulkMouseUpdateForm(selected_mice=[self.mouse])
        bulk.fields[self.field].clean("custom")
        self.assertIn({"value": "custom", "label": "Custom result"}, self.choices_response()["choices"])
        self.mouse.refresh_from_db()
        self.assertEqual(self.mouse.genotype, "Test line: Custom result")
        self.assertEqual(self.mouse.genotype_calls.get().get_zygosity_display(), "Custom result")

    def test_retired_category_preserves_existing_calls(self):
        category = GenotypeCategory.objects.get(code="het")
        sync_mouse_structured_genetics(self.mouse, self.crossing, {self.line.pk: "het"})
        category.active = False
        category.save()
        self.assertNotIn(("het", "Het"), genotype_category_choices())
        form = MouseEditForm(instance=self.mouse)
        self.assertEqual(form.fields[self.field].clean("het"), "het")
        bulk = BulkMouseUpdateForm(selected_mice=[self.mouse])
        self.assertEqual(bulk.fields[self.field].clean("het"), "het")
        self.assertIn({"value": "het", "label": "Het"}, self.choices_response()["choices"])
        self.assertEqual(self.mouse.genotype_calls.get().get_zygosity_display(), "Het")

    def test_labels_refresh_and_invalid_codes_are_rejected(self):
        category = GenotypeCategory.objects.get(code="het")
        category.label = "Heterozygous"
        category.save()
        sync_mouse_structured_genetics(self.mouse, self.crossing, {self.line.pk: "het"})
        self.assertEqual(self.mouse.genotype, "Test line: Heterozygous")
        call = self.mouse.genotype_calls.get()
        call.full_clean()
        call.zygosity = "invalid"
        with self.assertRaises(ValidationError):
            call.full_clean()
        with self.assertRaises(ValidationError):
            MouseEditForm(instance=self.mouse).fields[self.field].clean("invalid")

    def test_admin_protects_codes_and_retains_unknown(self):
        category = GenotypeCategory.objects.get(code="unknown")
        category.active = False
        with self.assertRaises(ValidationError):
            category.full_clean()
        category.active = True
        category.code = "different"
        with self.assertRaises(ValidationError):
            category.full_clean()
        admin = GenotypeCategoryAdmin(GenotypeCategory, AdminSite())
        self.assertEqual(admin.get_readonly_fields(None, category), ("code",))
        self.assertFalse(admin.has_delete_permission(None, category))
