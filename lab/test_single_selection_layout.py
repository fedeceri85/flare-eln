from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import genotype_field_name, individual_mouse_field_prefix, sync_mouse_structured_genetics
from .models import Mouse, MouseLine, Protocol, Species, resolve_crossing
from .test_navigation import Markup


@override_settings(ALLOWED_HOSTS=["testserver"])
class SingleSelectionLayoutTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username="single-selection"))
        self.protocol = Protocol.objects.create(protocol_number=3, licence_reference="SINGLE", name="Single")
        species = Species.objects.create(name="Single selection species")
        self.line = MouseLine.objects.create(species=species, name="Gene A")
        self.crossing, _ = resolve_crossing(species, [self.line])
        self.mouse = Mouse.objects.create(mouse_id="Known / 1", protocol=self.protocol, sex="F", tattoo="007",
            date_of_birth=date(2026, 1, 2), breeding_pair="Pair A", status="Alive", severity="Mild",
            protocol_start_date=date(2026, 2, 3), cull_date=date(2026, 3, 4), notes="Current notes")
        sync_mouse_structured_genetics(self.mouse, self.crossing, {self.line.pk: "het"}, uncertainty_values={self.line.pk: True})
        self.other = Mouse.objects.create(mouse_id="Known 2", protocol=self.protocol)
        self.stem = individual_mouse_field_prefix(self.mouse)
        self.gene = genotype_field_name(self.line)
        self.urls = [reverse("lab:edit_selected_mice_all"), reverse("lab:edit_selected_mice_licence_protocol", args=["SINGLE", 3])]

    def get(self, url, mice=None):
        return self.client.get(url, {"selected_mouse": [mouse.pk for mouse in (mice or [self.mouse])]})

    def test_single_selection_embeds_one_table_in_first_tab_without_duplicate_controls(self):
        for url in self.urls:
            response = self.get(url)
            self.assertEqual(response.status_code, 200)
            html = Markup(response.content.decode())
            self.assertEqual([node.attrs["data-mouse-tab"] for node in html.find("button", role="tab")], ["ids", "shared", "procedures", "services"])
            self.assertIn("Animal details", html.find("button", id="mouse-tab-shared")[0].text)
            self.assertFalse(html.find("section", id="mouse-panel-individual"))
            local = html.find("input", name=self.stem + "_local_identifier")[0]
            sex = html.find("select", name=self.stem + "_sex")[0]
            gene = html.find("select", name=f"{self.stem}_{self.gene}")[0]
            for field in (local, sex, gene):
                self.assertTrue(field.within("section", id="mouse-panel-shared"))
                self.assertTrue(field.within("section", id="mouse-individual-details"))
            self.assertLess(html.elements.index(gene), html.elements.index(html.find("select", name="status")[0]))
            ids = [node.attrs["id"] for node in html.elements if "id" in node.attrs]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertFalse(html.find("select", name=self.gene))
            self.assertEqual(len(html.find("input", name="update_genotype")), 1)

    def test_current_values_prefill_shared_fields_and_table(self):
        response = self.get(self.urls[0])
        form = response.context["form"]
        for name, value in {"status": "Alive", "date_of_birth": date(2026, 1, 2), "breeding_pair": "Pair A",
                            "severity": "Mild", "protocol_start_date": date(2026, 2, 3), "cull_date": date(2026, 3, 4),
                            "notes": "Current notes", "crossing_definition": self.crossing.pk,
                            "protocol": self.protocol.pk, self.stem + "_local_identifier": "007", self.stem + "_sex": "F",
                            f"{self.stem}_{self.gene}": "het", f"{self.stem}_{self.gene}_uncertain": True}.items():
            self.assertEqual(form[name].value(), value, name)
        html = Markup(response.content.decode())
        self.assertEqual(html.find("input", name="date_of_birth")[0].attrs["value"], "2026-01-02")
        self.assertFalse(form["update_status"].value())

    def test_multiple_selection_keeps_individual_tab_and_prefills_shared_status(self):
        response = self.get(self.urls[0], [self.mouse, self.other])
        html = Markup(response.content.decode())
        self.assertEqual(len(html.find("button", role="tab")), 5)
        self.assertTrue(html.find("section", id="mouse-panel-individual"))
        self.assertFalse(html.find("section", id="mouse-individual-details"))
        self.assertEqual(response.context["form"]["status"].value(), "Alive")

    def test_single_row_changes_save_without_modifying_unchecked_details(self):
        for index, url in enumerate(self.urls):
            sex, identifier, genotype = ("M", "L/R", "hom") if index == 0 else ("F", "Ear", "wt")
            response = self.client.post(url, {"selected_mouse": [self.mouse.pk], "apply_bulk_edit": "1",
                self.stem + "_present": "on", self.stem + "_sex": sex, self.stem + "_local_identifier": identifier,
                f"{self.stem}_{self.gene}": genotype, "crossing_definition": self.crossing.pk,
                "status": "Wrong but unchecked"})
            self.assertEqual(response.status_code, 302, response.context["form"].errors if response.context else "")
            self.mouse.refresh_from_db()
            self.assertEqual((self.mouse.sex, self.mouse.tattoo, self.mouse.status), (sex, identifier, "Alive"))
            call = self.mouse.genotype_calls.get(mouse_line=self.line)
            self.assertEqual(call.zygosity, genotype)
            self.assertFalse(call.is_uncertain)
            self.assertEqual(self.mouse.notes, "Current notes")

    def test_validation_keeps_bound_row_edits_and_defaults_for_omitted_unchanged_fields(self):
        response = self.client.post(self.urls[0], {"selected_mouse": [self.mouse.pk], "apply_bulk_edit": "1",
            self.stem + "_sex": "invalid", self.stem + "_local_identifier": "Unsaved", "update_status": "on", "status": "Edited"})
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form[self.stem + "_local_identifier"].value(), "Unsaved")
        self.assertEqual(form["status"].value(), "Edited")
        self.assertEqual(form["breeding_pair"].value(), "Pair A")
        self.assertEqual(form["date_of_birth"].value(), "2026-01-02")
        self.assertFalse(Markup(response.content.decode()).find("button", id="mouse-tab-individual"))
        self.mouse.refresh_from_db()
        self.assertEqual(self.mouse.tattoo, "007")

    def test_ajax_refresh_updates_embedded_table_without_saving_other_fields(self):
        new_line = MouseLine.objects.create(species=self.line.species, name="Gene B")
        new_crossing, _ = resolve_crossing(self.line.species, [self.line, new_line])
        response = self.client.post(self.urls[0], {"selected_mouse": [self.mouse.pk], "apply_bulk_edit": "1",
            "preview_individual_mice": "1", "update_crossing": "on", "crossing_definition": new_crossing.pk,
            self.stem + "_local_identifier": "Draft", f"{self.stem}_{self.gene}": "hom", "update_status": "on", "status": "Draft status"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        html = Markup(response.json()["html"])
        self.assertEqual(html.find("input", name=self.stem + "_local_identifier")[0].attrs["value"], "Draft")
        self.assertTrue(html.find("select", name=f"{self.stem}_{genotype_field_name(new_line)}"))
        self.mouse.refresh_from_db()
        self.assertEqual((self.mouse.tattoo, self.mouse.status, self.mouse.crossing_definition_id), ("007", "Alive", self.crossing.pk))

    def test_old_single_editor_redirects_to_unified_layout(self):
        editor = self.client.get(reverse("lab:edit_mouse", args=[self.mouse.pk]), follow=True)
        self.assertEqual(editor.status_code, 200)
        self.assertTemplateUsed(editor, "lab/edit_selected_mice.html")
        self.assertEqual(editor.context["selected_mice"], [self.mouse])
        self.assertContains(editor, 'data-mouse-tab="ids"')
        self.assertNotContains(editor, 'id="mouse-tab-individual"')

