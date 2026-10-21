from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import LitterCreateForm, genotype_field_name
from .models import Mouse, MouseLine, Protocol, Species, resolve_crossing
from .test_navigation import Markup


@override_settings(ALLOWED_HOSTS=["testserver"])
class LitterSharedGenotypeTests(TestCase):
    def setUp(self):
        self.protocol = Protocol.objects.create(protocol_number=7, licence_reference="SHARED", name="Test")
        species = Species.objects.create(name="Shared genotype species")
        self.lines = [MouseLine.objects.create(species=species, name=name) for name in ("Gene A", "Gene B")]
        self.crossing, _ = resolve_crossing(species, self.lines)
        self.gene = genotype_field_name(self.lines[0])
        self.client.force_login(get_user_model().objects.create_user(username="shared-litter"))
        self.url = reverse("lab:add_litter_all")

    def payload(self, **extra):
        return {"mouse_id_prefix": "Shared-", "mouse_numbers": "1-2", "protocol": self.protocol.pk,
                "crossing_definition": self.crossing.pk, **extra}

    def test_shared_controls_render_beside_crossing_in_both_editors(self):
        urls = [self.url, reverse("lab:add_litter_licence_protocol", kwargs={
            "licence_reference": self.protocol.licence_reference, "protocol_number": 7})]
        for url in urls:
            response = self.client.post(url, self.payload(preview_mice="1"))
            html = Markup(response.content.decode())
            fields = html.find("select", name=self.gene)
            self.assertEqual(len(fields), 1)
            self.assertTrue(fields[0].within("div", **{"class": "shared-genotype-column"}))
            self.assertNotContains(response, 'name="use_shared_genotype"')
            self.assertContains(response, 'data-individual-genotype-field="true"')
            uncertainty = html.find("input", name=self.gene + "_uncertain")[0]
            self.assertEqual(uncertainty.attrs["data-line-id"], str(self.lines[0].pk))
            self.assertFalse(Mouse.objects.exists())

    def test_shared_genotype_and_uncertainty_seed_each_new_mouse(self):
        response = self.client.post(self.url, self.payload(**{
            self.gene: "het", self.gene + "_uncertain": "on",
            genotype_field_name(self.lines[1]): "wt",
        }))
        self.assertEqual(response.status_code, 302)
        for mouse in Mouse.objects.all():
            call = mouse.genotype_calls.get(mouse_line=self.lines[0])
            self.assertEqual(call.zygosity, "het")
            self.assertTrue(call.is_uncertain)
            self.assertEqual(mouse.genotype_calls.get(mouse_line=self.lines[1]).zygosity, "wt")

    def test_omitted_shared_values_leave_new_mice_unknown(self):
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 302)
        for mouse in Mouse.objects.all():
            call = mouse.genotype_calls.get(mouse_line=self.lines[0])
            self.assertEqual(call.zygosity, "unknown")
            self.assertFalse(call.is_uncertain)

    def test_explicit_individual_values_including_unknown_and_cleared_uncertainty_win(self):
        response = self.client.post(self.url, self.payload(**{
            self.gene: "het", self.gene + "_uncertain": "on",
            f"pup_1_{self.gene}": "hom", f"pup_2_{self.gene}": "unknown",
        }))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Mouse.objects.get(pk="Shared-1").genotype_calls.get(mouse_line=self.lines[0]).zygosity, "hom")
        call = Mouse.objects.get(pk="Shared-2").genotype_calls.get(mouse_line=self.lines[0])
        self.assertEqual(call.zygosity, "unknown")
        self.assertFalse(call.is_uncertain)

    def test_ajax_preview_returns_shared_fields_and_individual_defaults_without_writing(self):
        response = self.client.post(self.url, self.payload(**{
            "preview_mice": "1", self.gene: "het",
        }), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertContains(response, "genotype_html")
        shared = Markup(response.json()["genotype_html"])
        self.assertTrue(shared.find("select", name=self.gene))
        rows = Markup(response.json()["html"])
        for number in (1, 2):
            options = rows.find("option", value="het", selected=None)
            self.assertTrue(any(option.within("select", name=f"pup_{number}_{self.gene}") for option in options))
        self.assertFalse(Mouse.objects.exists())

    def test_failed_validation_keeps_individual_overrides(self):
        response = self.client.post(self.url, self.payload(**{
            "protocol": "", self.gene: "het", f"pup_1_{self.gene}": "hom",
        }))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"][f"pup_1_{self.gene}"].value(), "hom")
        self.assertEqual(response.context["form"][self.gene].value(), "het")
        self.assertFalse(Mouse.objects.exists())

    def test_without_javascript_explicit_apply_copies_to_existing_rows_but_never_saves(self):
        response = self.client.post(self.url, self.payload(**{
            "apply_shared_genotype": "1", self.gene: "wt", f"pup_1_{self.gene}": "hom",
        }))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"][f"pup_1_{self.gene}"].value(), "wt")
        self.assertFalse(Mouse.objects.exists())

    def test_invalid_shared_category_rejects_creation(self):
        form = LitterCreateForm(self.payload(**{self.gene: "invalid"}))
        self.assertFalse(form.is_valid())
        self.assertIn(self.gene, form.errors)
        self.assertFalse(Mouse.objects.exists())
