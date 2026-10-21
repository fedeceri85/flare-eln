from importlib import import_module

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import BulkMouseUpdateForm, LitterCreateForm, MouseEditForm, genotype_field_name, individual_mouse_field_prefix, sync_mouse_structured_genetics
from .models import GenotypeCategory, Mouse, MouseLine, Protocol, Species, resolve_crossing
from .views import apply_bulk_mouse_updates


@override_settings(ALLOWED_HOSTS=["testserver"])
class BulkIndividualMouseTests(TestCase):
    def setUp(self):
        self.protocol = Protocol.objects.create(protocol_number=7, licence_reference="BULK-ROW", name="Test")
        species = Species.objects.create(name="Bulk row species")
        self.lines = [MouseLine.objects.create(species=species, name=name) for name in ("Gene A", "Gene B")]
        self.crossing, _ = resolve_crossing(species, self.lines)
        self.mice = [Mouse.objects.create(mouse_id=mouse_id, sex=sex, protocol=self.protocol)
                     for mouse_id, sex in (("Mouse A / 1", "M"), ("Mouse B", "F"))]
        for mouse, category in zip(self.mice, ("het", "hom")):
            sync_mouse_structured_genetics(mouse, self.crossing, {line.pk: category for line in self.lines},
                                           uncertainty_values={self.lines[0].pk: mouse == self.mice[0]})
        self.user = get_user_model().objects.create_user(username="bulk-row-test")
        self.client.force_login(self.user)
        self.url = reverse("lab:edit_selected_mice_all")

    def name(self, index, suffix):
        return f"{individual_mouse_field_prefix(self.mice[index])}_{suffix}"

    def row_data(self):
        form = BulkMouseUpdateForm(selected_mice=self.mice)
        data = {}
        for name in form.fields:
            if not name.startswith("individual_"):
                continue
            value = form[name].value()
            if value is True:
                data[name] = "on"
            elif value is not False and value is not None:
                data[name] = value
        return data

    def apply(self, data, mice=None):
        form = BulkMouseUpdateForm(data, selected_mice=mice or self.mice)
        self.assertTrue(form.is_valid(), form.errors)
        apply_bulk_mouse_updates(mice or self.mice, form)
        for mouse in self.mice:
            mouse.refresh_from_db()
        return form

    def test_rows_render_current_values_for_each_mouse(self):
        response = self.client.get(self.url, {"selected_mouse": [mouse.pk for mouse in self.mice]})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Individual mice")
        self.assertContains(response, f'name="{self.name(0, "sex")}"')
        form = response.context["form"]
        self.assertEqual([row["sex"].value() for row in form.individual_rows], ["M", "F"])
        gene = genotype_field_name(self.lines[0])
        self.assertEqual(form[self.name(0, gene)].value(), "het")
        self.assertTrue(form[self.name(0, f"{gene}_uncertain")].value())

    def test_sex_changes_only_selected_row_without_shared_toggle(self):
        data = self.row_data()
        data[self.name(0, "sex")] = "F"
        self.apply(data)
        self.assertEqual([mouse.sex for mouse in self.mice], ["F", "F"])
        self.assertEqual(self.mice[1].genotype_calls.get(mouse_line=self.lines[0]).zygosity, "hom")

    def test_sex_can_be_cleared_to_unknown(self):
        data = self.row_data()
        data[self.name(1, "sex")] = ""
        self.apply(data)
        self.assertEqual([mouse.sex for mouse in self.mice], ["M", ""])

    def test_genotype_and_uncertainty_change_only_one_gene_and_mouse(self):
        data = self.row_data()
        gene = genotype_field_name(self.lines[0])
        data[self.name(0, gene)] = "cre"
        data.pop(self.name(0, f"{gene}_uncertain"))
        self.apply(data)
        call = self.mice[0].genotype_calls.get(mouse_line=self.lines[0])
        self.assertEqual(call.zygosity, "cre")
        self.assertFalse(call.is_uncertain)
        self.assertEqual(self.mice[0].genotype_calls.get(mouse_line=self.lines[1]).zygosity, "het")
        self.assertEqual(self.mice[1].genotype_calls.get(mouse_line=self.lines[0]).zygosity, "hom")
        self.assertIn("Gene A: Cre", self.mice[0].genetics_genotype_display)

    def test_missing_row_fields_preserve_existing_flags(self):
        gene = genotype_field_name(self.lines[0])
        self.apply({self.name(0, gene): "cre"})
        self.assertTrue(self.mice[0].genotype_calls.get(mouse_line=self.lines[0]).is_uncertain)
        self.assertEqual(self.mice[1].sex, "F")

    def test_displayed_individual_values_are_saved_over_shared_settings(self):
        data = self.row_data()
        gene = genotype_field_name(self.lines[0])
        data.update({
            self.name(0, "sex"): "F",
            "update_genotype": "on", "crossing_definition": self.crossing.pk,
            gene: "wt", self.name(0, gene): "hom",
        })
        data.pop(self.name(0, f"{gene}_uncertain"))
        self.apply(data)
        self.assertEqual([mouse.sex for mouse in self.mice], ["F", "F"])
        calls = [mouse.genotype_calls.get(mouse_line=self.lines[0]) for mouse in self.mice]
        self.assertEqual([(call.zygosity, call.is_uncertain) for call in calls], [("hom", False), ("hom", False)])

    def test_mixed_crossings_can_be_edited_without_shared_crossing(self):
        other_line = MouseLine.objects.create(species=self.lines[0].species, name="Gene C")
        other_crossing, _ = resolve_crossing(other_line.species, [other_line])
        sync_mouse_structured_genetics(self.mice[1], other_crossing, {other_line.pk: "wt"})
        data = self.row_data()
        data[self.name(1, genotype_field_name(other_line))] = "cre"
        self.apply(data)
        self.assertEqual(self.mice[0].crossing_definition_id, self.crossing.pk)
        self.assertEqual(self.mice[1].crossing_definition_id, other_crossing.pk)
        self.assertEqual(self.mice[1].genotype_calls.get(mouse_line=other_line).zygosity, "cre")

    def test_row_identity_survives_reordered_selection(self):
        data = self.row_data()
        data[self.name(0, "sex")] = "F"
        self.apply(data, list(reversed(self.mice)))
        self.assertEqual([mouse.sex for mouse in self.mice], ["F", "F"])

    def test_invalid_individual_category_prevents_any_changes(self):
        data = self.row_data()
        data[self.name(0, genotype_field_name(self.lines[0]))] = "made-up"
        data.update({"apply_bulk_edit": "1", "selected_mouse": [mouse.pk for mouse in self.mice], "update_sex": "on", "sex": "F"})
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.mice[0].refresh_from_db()
        self.assertEqual(self.mice[0].sex, "M")

    def test_refresh_new_crossing_shows_new_genes_without_saving(self):
        line = MouseLine.objects.create(species=self.lines[0].species, name="New Gene")
        crossing, _ = resolve_crossing(line.species, [line])
        data = self.row_data()
        data.update({"apply_bulk_edit": "1", "preview_individual_mice": "1", "update_crossing": "on",
                     "crossing_definition": crossing.pk, "selected_mouse": [mouse.pk for mouse in self.mice]})
        response = self.client.post(self.url, data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.name(0, genotype_field_name(line)), response.json()["html"])
        self.mice[0].refresh_from_db()
        self.assertEqual(self.mice[0].crossing_definition_id, self.crossing.pk)
        data.pop("preview_individual_mice")
        data[self.name(0, genotype_field_name(line))] = "cre"
        self.apply(data)
        self.assertEqual(self.mice[0].genotype_calls.get().zygosity, "cre")
        self.assertEqual(self.mice[1].genotype_calls.get().zygosity, "unknown")

    def test_scoped_bulk_route_saves_individual_sex(self):
        url = reverse("lab:edit_selected_mice_licence_protocol", kwargs={
            "licence_reference": self.protocol.licence_reference, "protocol_number": self.protocol.protocol_number,
        })
        data = self.row_data()
        data.update({self.name(0, "sex"): "F", "apply_bulk_edit": "1", "selected_mouse": [mouse.pk for mouse in self.mice]})
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.mice[0].refresh_from_db()
        self.assertEqual(self.mice[0].sex, "F")

    def test_retired_categories_stay_available_on_existing_rows(self):
        GenotypeCategory.objects.filter(code="het").update(active=False)
        form = BulkMouseUpdateForm(selected_mice=self.mice)
        choices = list(form.fields[self.name(0, genotype_field_name(self.lines[0]))].choices)
        self.assertIn(("het", "Het"), choices)

    def test_other_is_default_and_none_is_removed_everywhere(self):
        self.assertEqual(Mouse(mouse_id="new", protocol=self.protocol).marking_type, "other")
        forms = [LitterCreateForm(), MouseEditForm(instance=self.mice[0])]
        for form in forms:
            self.assertEqual(form.fields["marking_type"].initial, "other")
            self.assertEqual(list(form.fields["marking_type"].choices), Mouse.MARKING_CHOICES)
            self.assertNotIn("None", str(form["marking_type"]))
        Mouse.objects.filter(pk=self.mice[0].pk).update(marking_type="")
        migration = import_module("lab.migrations.0049_alter_mouse_marking_type")
        migration.default_empty_types_to_other(apps, connection.schema_editor())
        self.mice[0].refresh_from_db()
        self.assertEqual(self.mice[0].marking_type, "other")

    def test_refresh_without_javascript_does_not_save_or_require_changes(self):
        data = self.row_data()
        data.update({"apply_bulk_edit": "1", "preview_individual_mice": "1", "selected_mouse": [mouse.pk for mouse in self.mice]})
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["form"].errors)
        self.mice[0].refresh_from_db()
        self.assertEqual(self.mice[0].sex, "M")

    def test_removed_shared_controls_are_absent_and_old_post_values_are_ignored(self):
        response = self.client.get(self.url, {"selected_mouse": [mouse.pk for mouse in self.mice]})
        for field in ("update_sex", "sex", "update_tattoo", "tattoo", "marking_type"):
            self.assertNotContains(response, f'name="{field}"')
        self.assertNotContains(response, "Leave uncertainty unchanged")
        self.assertNotContains(response, 'name="genotype_line_1_uncertain"')
        original = [(mouse.sex, mouse.tattoo) for mouse in self.mice]
        self.apply({"update_sex": "on", "sex": "M", "update_tattoo": "on", "tattoo": "same-id",
                    "notes_action": "append", "notes": "Shared note"})
        self.assertEqual([(mouse.sex, mouse.tattoo) for mouse in self.mice], original)

    def test_row_reverted_to_original_value_is_not_overwritten_by_shared_genotype(self):
        data = self.row_data()
        gene = genotype_field_name(self.lines[0])
        data.update({"update_genotype": "on", "crossing_definition": self.crossing.pk, gene: "wt"})
        # The rows still show their original values, e.g. after an explicit individual adjustment.
        self.apply(data)
        self.assertEqual([mouse.genotype_calls.get(mouse_line=self.lines[0]).zygosity for mouse in self.mice],
                         ["het", "hom"])

    def test_synchronized_row_values_are_saved_and_keep_individual_uncertainty(self):
        data = self.row_data()
        gene = genotype_field_name(self.lines[0])
        data.update({"update_genotype": "on", "crossing_definition": self.crossing.pk, gene: "wt"})
        for index in range(2):
            data[self.name(index, gene)] = "wt"
        self.apply(data)
        calls = [mouse.genotype_calls.get(mouse_line=self.lines[0]) for mouse in self.mice]
        self.assertEqual([(call.zygosity, call.is_uncertain) for call in calls], [("wt", True), ("wt", False)])

    def test_no_javascript_refresh_applies_shared_genotype_to_visible_rows(self):
        data = self.row_data()
        gene = genotype_field_name(self.lines[0])
        data.update({"apply_bulk_edit": "1", "preview_individual_mice": "1", "update_genotype": "on",
                     "crossing_definition": self.crossing.pk, gene: "wt", "selected_mouse": [mouse.pk for mouse in self.mice]})
        response = self.client.post(self.url, data)
        form = response.context["form"]
        self.assertEqual([form[self.name(index, gene)].value() for index in range(2)], ["wt", "wt"])
        self.assertEqual(self.mice[0].genotype_calls.get(mouse_line=self.lines[0]).zygosity, "het")

    def test_shared_genotype_cannot_change_mixed_crossings_without_update_crossing(self):
        line = MouseLine.objects.create(species=self.lines[0].species, name="Separate Gene")
        crossing, _ = resolve_crossing(line.species, [line])
        sync_mouse_structured_genetics(self.mice[1], crossing, {line.pk: "wt"})
        form = BulkMouseUpdateForm({"update_genotype": "on", "crossing_definition": self.crossing.pk,
                                   genotype_field_name(self.lines[0]): "het"}, selected_mice=self.mice)
        self.assertFalse(form.is_valid())
        self.assertIn("crossing_search", form.errors)
