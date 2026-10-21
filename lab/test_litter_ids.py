import json
from datetime import date
from importlib import import_module

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .forms import BulkMouseUpdateForm, LitterCreateForm, MouseEditForm, genotype_field_name
from .models import Mouse, MouseLine, ProcedureType, Protocol, Species, resolve_crossing
from .utils import parse_mouse_numbers
from .views import apply_bulk_mouse_updates, create_litter_mice


class MouseNumberParsingTests(SimpleTestCase):
    def test_lists_ranges_order_and_leading_zeros(self):
        self.assertEqual(parse_mouse_numbers(" 5, 1-3, 008-010 "),
                         ["5", "1", "2", "3", "008", "009", "010"])

    def test_invalid_ambiguous_or_oversized_input(self):
        for value in ("", "1,", "a", "-1", "1.5", "3-1", "1-2-3", "1,1", "1-3,2", "0-500", "1-9999999999999999999"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_mouse_numbers(value)
        self.assertEqual(len(parse_mouse_numbers("1-500")), 500)


@override_settings(ALLOWED_HOSTS=["testserver"])
class LitterIdentityTests(TestCase):
    def setUp(self):
        self.protocol = Protocol.objects.create(protocol_number=1, licence_reference="LITTER", name="Test")
        species = Species.objects.create(name="Litter test")
        self.lines = [MouseLine.objects.create(species=species, name=name) for name in ("Gene A", "Gene B")]
        self.crossing, _ = resolve_crossing(species, self.lines)
        self.user = get_user_model().objects.create_user(username="litter-test")
        self.client.force_login(self.user)
        self.url = reverse("lab:add_litter_all")

    def payload(self, **extra):
        return {
            "mouse_numbers": "1-2", "mouse_id_prefix": "PUP-",
            "date_of_birth": "2026-10-21", "breeding_pair": "BP 4",
            "crossing_definition": self.crossing.pk, "protocol": self.protocol.pk,
            "status": "Alive", **extra,
        }

    def valid_form(self, **extra):
        form = LitterCreateForm(self.payload(**extra))
        self.assertTrue(form.is_valid(), form.errors)
        return form

    def test_form_replaces_old_controls_and_includes_other(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        for field in ("mouse_numbers", "mouse_id_prefix", "no_id_available"):
            self.assertContains(response, f'name="{field}"')
        for field in ("mouse_count", "tattoo_start", "mouse_id_start", "use_progressive_mouse_ids"):
            self.assertNotContains(response, f'name="{field}"')
        response = self.client.post(self.url, self.payload(preview_mice="1"))
        self.assertContains(response, 'value="other"')
        self.assertFalse(Mouse.objects.exists())

    def test_blank_prefix_and_multiple_unmarked_pups(self):
        form = self.valid_form(mouse_id_prefix="", mouse_numbers="101,103-104")
        mice, _ = create_litter_mice(self.protocol, form)
        self.assertEqual([mouse.pk for mouse in mice], ["101", "103", "104"])
        self.assertTrue(all(mouse.tattoo == "" and mouse.marking_type == "other" for mouse in mice))
        self.assertEqual(Mouse.objects.count(), 3)

    def test_shared_local_id_type_with_individual_identifiers_sex_and_genotype(self):
        gene = genotype_field_name(self.lines[0])
        form = self.valid_form(**{
            "mouse_numbers": "1,3-4", "marking_type": "other", "pup_1_marking": "012", "pup_1_sex": "F",
            "pup_3_marking": "L2R1", "pup_3_sex": "M",
            "pup_4_marking": "blue mark",
            f"pup_1_{gene}": "het", f"pup_1_{gene}_uncertain": "on", f"pup_3_{gene}": "hom",
        })
        mice, _ = create_litter_mice(self.protocol, form)
        self.assertEqual([mouse.pk for mouse in mice], ["PUP-1", "PUP-3", "PUP-4"])
        self.assertEqual([(mouse.marking_type, mouse.tattoo, mouse.sex) for mouse in mice],
                         [("other", "012", "F"), ("other", "L2R1", "M"), ("other", "blue mark", "")])
        self.assertEqual(mice[0].genotype_calls.get(mouse_line=self.lines[0]).zygosity, "het")
        self.assertTrue(mice[0].genotype_calls.get(mouse_line=self.lines[0]).is_uncertain)
        self.assertEqual(mice[1].genotype_calls.get(mouse_line=self.lines[0]).zygosity, "hom")
        self.assertEqual(mice[2].genotype_calls.get(mouse_line=self.lines[0]).zygosity, "unknown")

    def test_generated_prefix_is_visible_and_enforced_on_server(self):
        form = self.valid_form(no_id_available="on", mouse_id_prefix="IGNORE-ME-")
        self.assertEqual(form.cleaned_data["mouse_id_prefix"], "20261021_bp-4_gene-a-x-gene-b_")
        self.assertIn('readonly', str(form["mouse_id_prefix"]))
        mice, _ = create_litter_mice(self.protocol, form)
        self.assertEqual(mice[0].pk, "20261021_bp-4_gene-a-x-gene-b_1")

    def test_generated_prefix_requires_birth_date_and_breeding_pair(self):
        form = LitterCreateForm(self.payload(no_id_available="on", date_of_birth="", breeding_pair=""))
        self.assertFalse(form.is_valid())
        self.assertIn("date_of_birth", form.errors)
        self.assertIn("breeding_pair", form.errors)
        self.valid_form(date_of_birth="", breeding_pair="", mouse_id_prefix="")

    def test_invalid_ids_do_not_create_mice(self):
        Mouse.objects.create(mouse_id="PUP-2", protocol=self.protocol)
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mouse IDs already exist")
        self.assertEqual(Mouse.objects.count(), 1)
        for extra in ({"mouse_numbers": "1,1"}, {"mouse_numbers": "3-1"},
                      {"mouse_id_prefix": "X" * 99, "mouse_numbers": "10"}):
            form = LitterCreateForm(self.payload(**extra))
            self.assertFalse(form.is_valid())

    def test_duplicate_local_identifiers_are_rejected_for_automatic_ids(self):
        form = LitterCreateForm(self.payload(no_id_available="on", marking_type="other", pup_1_marking="tag", pup_2_marking="tag"))
        self.assertFalse(form.is_valid())
        self.assertIn("pup_2_marking", form.errors)

    def test_other_is_default_and_identifiers_remain_optional(self):
        form = self.valid_form(pup_1_marking="12")
        self.assertEqual(form.cleaned_data["marking_type"], "other")
        self.valid_form(marking_type="tattoo")
        self.assertEqual(LitterCreateForm().fields["marking_type"].initial, "other")
        self.assertNotIn(("", "None"), list(form.fields["marking_type"].choices))

    def test_ajax_preview_preserves_fields_and_never_saves(self):
        gene = genotype_field_name(self.lines[0])
        response = self.client.post(self.url, self.payload(**{
            "preview_mice": "1", "no_id_available": "on", "marking_type": "other", "pup_1_marking": "blue",
            f"pup_1_{gene}": "het", f"pup_1_{gene}_uncertain": "on",
        }), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        result = response.json()
        self.assertIn("20261021_bp-4_gene-a-x-gene-b_1", result["html"])
        self.assertIn('value="blue"', result["html"])
        self.assertIn('value="het" selected', result["html"])
        self.assertIn('checked', result["html"])
        self.assertEqual(result["prefix"], "20261021_bp-4_gene-a-x-gene-b_")
        self.assertFalse(Mouse.objects.exists())

    def test_save_creates_batch_and_optional_procedures(self):
        procedure_type = ProcedureType.objects.create(name="Litter procedure")
        response = self.client.post(self.url, self.payload(procedure_drafts=json.dumps([{"procedure_type": procedure_type.pk}])))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Mouse.objects.count(), 2)
        self.assertTrue(all(mouse.procedures.count() == 1 for mouse in Mouse.objects.all()))

    def test_existing_local_identifiers_are_preserved_as_other(self):
        mouse = Mouse.objects.create(mouse_id="LEGACY", tattoo="L2R1", protocol=self.protocol)
        migration = import_module("lab.migrations.0047_mouse_markings")
        migration.preserve_existing_markings(apps, connection.schema_editor())
        mouse.refresh_from_db()
        self.assertEqual((mouse.tattoo, mouse.marking_type), ("L2R1", "other"))
        form = MouseEditForm(instance=mouse)
        self.assertEqual(form["marking_type"].value(), "other")

    def test_shared_bulk_changes_do_not_clear_local_identifier(self):
        mouse = Mouse.objects.create(mouse_id="MARKED", tattoo="blue", marking_type="other", protocol=self.protocol)
        form = BulkMouseUpdateForm({"update_tattoo": "on", "tattoo": "", "notes_action": "append", "notes": "Note"}, selected_mice=[mouse])
        self.assertTrue(form.is_valid(), form.errors)
        apply_bulk_mouse_updates([mouse], form)
        mouse.refresh_from_db()
        self.assertEqual((mouse.tattoo, mouse.marking_type), ("blue", "other"))

    def test_invalid_preview_reports_error_without_creating_mice(self):
        response = self.client.post(self.url, self.payload(preview_mice="1", mouse_numbers="1,1"),
                                    HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        self.assertIn("listed more than once", response.json()["errors"][0])
        self.assertFalse(Mouse.objects.exists())

    def test_single_mouse_can_save_other_marking(self):
        mouse = Mouse.objects.create(mouse_id="SINGLE", protocol=self.protocol)
        form = MouseEditForm({
            "protocol": self.protocol.pk, "status": "Alive", "crossing_definition": self.crossing.pk,
            "marking_type": "other", "tattoo": "blue tag",
        }, instance=mouse)
        self.assertTrue(form.is_valid(), form.errors)
        mouse = form.save()
        self.assertEqual((mouse.marking_type, mouse.tattoo), ("other", "blue tag"))

    def test_local_id_type_is_rendered_once_in_shared_details(self):
        response = self.client.post(self.url, self.payload(preview_mice="1", marking_type="other"))
        html = response.content.decode()
        self.assertEqual(html.count('name="marking_type"'), 1)
        self.assertLess(html.index('name="marking_type"'), html.index('id="mouse-panel-individual"'))
        self.assertIn("Local ID type", html)
        self.assertIn("Local identifier", html)
        self.assertNotIn('name="pup_1_marking_type"', html)
        self.assertNotIn("Marking type", html)

    def test_each_local_id_type_is_applied_to_the_entire_litter(self):
        for index, local_id_type in enumerate(("tattoo", "ear_clip", "other")):
            with self.subTest(local_id_type=local_id_type):
                form = self.valid_form(mouse_id_prefix=f"TYPE-{index}-", marking_type=local_id_type,
                                       pup_1_marking="01", pup_2_marking="02", breeding_pair=f"Pair {index}")
                mice, _ = create_litter_mice(self.protocol, form)
                self.assertEqual([mouse.marking_type for mouse in mice], [local_id_type, local_id_type])
                self.assertEqual([mouse.tattoo for mouse in mice], ["01", "02"])
