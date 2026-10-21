import json

from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase

from .forms import (
    BulkMouseUpdateForm, LitterCreateForm, MouseEditForm, MouseForm, genotype_field_name, individual_mouse_field_prefix,
    sync_mouse_structured_genetics,
)
from .models import Mouse, MouseLine, Protocol, Species, resolve_crossing
from .views import apply_bulk_mouse_updates, create_litter_mice, crossing_genotype_fields


class GenotypeUncertaintyTests(TestCase):
    def setUp(self):
        species = Species.objects.create(name="Uncertainty test species")
        self.lines = [MouseLine.objects.create(species=species, name=name)
                      for name in ("Gene A", "Gene B")]
        self.crossing, _ = resolve_crossing(species, self.lines)
        self.protocol = Protocol.objects.create(
            protocol_number=1, licence_reference="UNC", name="Uncertainty test",
        )
        self.mouse = Mouse.objects.create(mouse_id="UNC-1", protocol=self.protocol)
        sync_mouse_structured_genetics(
            self.mouse, self.crossing, {line.pk: "het" for line in self.lines},
        )
        self.field = genotype_field_name(self.lines[0])
        self.uncertain_field = f"{self.field}_uncertain"

    def mouse_data(self, **extra):
        return {
            "protocol": self.protocol.pk,
            "status": "Alive",
            "crossing_definition": self.crossing.pk,
            "crossing_search": self.crossing.display_name,
            **{genotype_field_name(line): "het" for line in self.lines},
            **extra,
        }

    def bulk_form(self, **extra):
        form = BulkMouseUpdateForm(self.mouse_data(update_genotype="on", **extra),
                                   selected_mice=[self.mouse])
        self.assertTrue(form.is_valid(), form.errors)
        return form

    def set_uncertain(self):
        sync_mouse_structured_genetics(
            self.mouse, self.crossing, {}, preserve_missing=True,
            uncertainty_values={self.lines[0].pk: True},
        )

    def test_existing_results_default_to_unmarked(self):
        self.assertFalse(self.mouse.genotype_calls.filter(is_uncertain=True).exists())
        self.assertNotIn("uncertain", self.mouse.genetics_genotype_display)

    def test_edit_marks_only_one_gene_and_can_clear_flag(self):
        form = MouseEditForm(self.mouse_data(**{self.uncertain_field: "on"}), instance=self.mouse)
        self.assertTrue(form.is_valid(), form.errors)
        form.save_structured_genetics(form.save())
        self.assertTrue(self.mouse.genotype_calls.get(mouse_line=self.lines[0]).is_uncertain)
        self.assertFalse(self.mouse.genotype_calls.get(mouse_line=self.lines[1]).is_uncertain)
        self.mouse.refresh_from_db()
        self.assertEqual(self.mouse.genotype, "Gene A: Het (uncertain); Gene B: Het")
        self.assertEqual(self.mouse.genetics_genotype_display, self.mouse.genotype)
        self.assertEqual(self.mouse.genotype_calls.get(mouse_line=self.lines[0]).get_zygosity_display(),
                         "Het (uncertain)")
        form = MouseEditForm(self.mouse_data(), instance=self.mouse)
        self.assertTrue(form.is_valid(), form.errors)
        form.save_structured_genetics(form.save())
        self.assertFalse(self.mouse.genotype_calls.filter(is_uncertain=True).exists())

    def test_create_form_accepts_uncertain_unknown(self):
        form = MouseForm(self.mouse_data(
            mouse_id="UNC-NEW", custom_metadata="{}",
            **{self.field: "unknown", self.uncertain_field: "on"},
        ))
        self.assertTrue(form.is_valid(), form.errors)
        mouse = form.save()
        form.save_structured_genetics(mouse)
        call = mouse.genotype_calls.get(mouse_line=self.lines[0])
        self.assertTrue(call.is_uncertain)
        self.assertEqual(call.zygosity, "unknown")

    def test_bulk_category_update_preserves_flags(self):
        self.set_uncertain()
        form = self.bulk_form(**{self.field: "hom"})
        apply_bulk_mouse_updates([self.mouse], form)
        call = self.mouse.genotype_calls.get(mouse_line=self.lines[0])
        self.assertEqual(call.zygosity, "hom")
        self.assertTrue(call.is_uncertain)

    def test_individual_checkbox_can_mark_and_clear_without_changing_category(self):
        stem = individual_mouse_field_prefix(self.mouse)
        for expected in (True, False):
            data = {f"{stem}_present": "on"}
            if expected:
                data[f"{stem}_{self.uncertain_field}"] = "on"
            form = BulkMouseUpdateForm(data, selected_mice=[self.mouse])
            self.assertTrue(form.is_valid(), form.errors)
            apply_bulk_mouse_updates([self.mouse], form)
            call = self.mouse.genotype_calls.get(mouse_line=self.lines[0])
            self.assertEqual(call.zygosity, "het")
            self.assertEqual(call.is_uncertain, expected)

    def test_omitted_uncertainty_is_preserved_by_sync(self):
        self.set_uncertain()
        sync_mouse_structured_genetics(self.mouse, self.crossing, {self.lines[0].pk: "hom"})
        self.assertTrue(self.mouse.genotype_calls.get(mouse_line=self.lines[0]).is_uncertain)

    def test_endpoint_and_rendered_controls_include_existing_flag(self):
        self.set_uncertain()
        response = crossing_genotype_fields(RequestFactory().get(
            "/", {"crossing": self.crossing.pk, "mouse": self.mouse.pk},
        ))
        line = json.loads(response.content)["lines"][0]
        self.assertTrue(line["is_uncertain"])
        self.assertEqual(line["uncertainty_field_name"], self.uncertain_field)
        form = MouseEditForm(instance=self.mouse)
        html = render_to_string("lab/components/form_fields.html", {"form": form})
        self.assertIn(f'name="{self.uncertain_field}"', html)
        self.assertEqual(html.count(f'name="{self.uncertain_field}"'), 1)
        self.assertIn('checked', str(form[self.uncertain_field]))
        self.assertIn("Uncertain", html)
        bulk = BulkMouseUpdateForm(selected_mice=[self.mouse])
        html = render_to_string("lab/edit_selected_mice.html", {"form": bulk, "selected_mice": [self.mouse]})
        self.assertIn(f'name="{individual_mouse_field_prefix(self.mouse)}_{self.uncertain_field}"', html)
        self.assertNotIn("Leave uncertainty unchanged", html)

    def test_litter_applies_flag_to_each_new_mouse(self):
        form = LitterCreateForm(self.mouse_data(
            mouse_numbers="1-2", mouse_id_prefix="UNC-LITTER-",
            **{f"pup_{number}_{self.uncertain_field}": "on" for number in (1, 2)},
        ))
        self.assertTrue(form.is_valid(), form.errors)
        html = render_to_string("lab/add_animals.html", {"form": form})
        self.assertEqual(html.count(f'name="pup_1_{self.uncertain_field}"'), 1)
        mice, _ = create_litter_mice(self.protocol, form)
        self.assertEqual(len(mice), 2)
        for mouse in mice:
            self.assertTrue(mouse.genotype_calls.get(mouse_line=self.lines[0]).is_uncertain)
            self.assertFalse(mouse.genotype_calls.get(mouse_line=self.lines[1]).is_uncertain)

    def test_bulk_preserves_different_flags_between_mice(self):
        self.set_uncertain()
        other = Mouse.objects.create(mouse_id="UNC-OTHER", protocol=self.protocol)
        sync_mouse_structured_genetics(other, self.crossing, {line.pk: "het" for line in self.lines})
        form = BulkMouseUpdateForm(self.mouse_data(update_genotype="on", **{self.field: "hom"}),
                                   selected_mice=[self.mouse, other])
        self.assertTrue(form.is_valid(), form.errors)
        apply_bulk_mouse_updates([self.mouse, other], form)
        self.assertTrue(self.mouse.genotype_calls.get(mouse_line=self.lines[0]).is_uncertain)
        self.assertFalse(other.genotype_calls.get(mouse_line=self.lines[0]).is_uncertain)
