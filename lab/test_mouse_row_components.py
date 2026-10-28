from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import genotype_field_name, individual_mouse_field_prefix, sync_mouse_structured_genetics
from .models import Mouse, MouseLine, Protocol, Species, resolve_crossing
from .test_navigation import Markup


@override_settings(ALLOWED_HOSTS=["testserver"])
class MouseRowComponentTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username="row-components"))
        self.protocol = Protocol.objects.create(protocol_number=6, licence_reference="ROWS", name="Rows")
        species = Species.objects.create(name="Row species")
        self.lines = [MouseLine.objects.create(species=species, name=name) for name in ("Gene A", "Gene B")]
        self.crossings = [resolve_crossing(species, [line])[0] for line in self.lines]
        self.mice = [Mouse.objects.create(mouse_id=f"Existing-{index}", tattoo=f"00{index}", sex="F", protocol=self.protocol)
                     for index in (1, 2)]
        for mouse, crossing, line in zip(self.mice, self.crossings, self.lines):
            sync_mouse_structured_genetics(mouse, crossing, {line.pk: "het"})

    def test_bulk_rows_keep_separate_crossing_captions_and_fields(self):
        response = self.client.get(reverse("lab:edit_selected_mice_all"), {"selected_mouse": [mouse.pk for mouse in self.mice]})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "lab/components/individual_mouse_table.html")
        html = Markup(response.content.decode())
        captions = html.find("caption", **{"class": "individual-crossing-label"})
        self.assertEqual(len(captions), 2)
        for mouse, crossing, line, caption in zip(self.mice, self.crossings, self.lines, captions):
            self.assertIn(crossing.display_name, caption.text)
            table = caption.parent
            headings = [node.text.strip() for node in html.find("th", scope="col") if node.parent.parent.parent is table]
            self.assertEqual(headings, ["Animal ID", "Local identifier", "Sex", "Genotype"])
            stem = individual_mouse_field_prefix(mouse)
            local = html.find("input", name=f"{stem}_local_identifier")[0]
            self.assertEqual(local.attrs["value"], mouse.tattoo)
            self.assertTrue(any(label.attrs.get("for") == local.attrs["id"] for label in html.find("label")))
            self.assertTrue(html.find("select", name=f"{stem}_{genotype_field_name(line)}"))

    def test_litter_rows_keep_input_names_values_errors_and_mouse_labels(self):
        response = self.client.post(reverse("lab:add_litter_all"), {
            "protocol": self.protocol.pk, "crossing_definition": self.crossings[0].pk,
            "mouse_numbers": "01-02", "mouse_id_prefix": "Pup-", "pup_01_marking": "L/R",
            "pup_01_sex": "F", "pup_02_sex": "invalid",
            f"pup_01_{genotype_field_name(self.lines[0])}": "hom",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "lab/components/individual_mouse_table.html")
        html = Markup(response.content.decode())
        identifier = html.find("input", name="pup_01_marking")[0]
        self.assertEqual(identifier.attrs["value"], "L/R")
        self.assertTrue(any(label.attrs.get("for") == identifier.attrs["id"] and "Pup-01" in label.text for label in html.find("label")))
        self.assertTrue(html.find("select", name="pup_02_sex"))
        self.assertContains(response, "Select a valid choice.")
        self.assertFalse(Mouse.objects.filter(mouse_id__startswith="Pup-").exists())

    def test_empty_crossing_keeps_individual_inputs_and_genotype_guidance(self):
        mouse = Mouse.objects.create(mouse_id="No crossing", protocol=self.protocol, sex="M")
        response = self.client.get(reverse("lab:edit_selected_mice_all"), {"selected_mouse": [mouse.pk]})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose a crossing to enter genotypes.")
        html = Markup(response.content.decode())
        self.assertFalse(html.find("caption", **{"class": "individual-crossing-label"}))
        self.assertTrue(html.find("select", name=f"{individual_mouse_field_prefix(mouse)}_sex"))
