"""Acceptance checks for shared creation/editing and atomic Mouse ID changes."""
from datetime import date
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import individual_mouse_field_prefix, sync_mouse_structured_genetics
from .models import Mouse, MouseLine, Procedure, ProcedureType, Protocol, Species, resolve_crossing
from .test_navigation import Markup
from .views.mice import rename_mouse_identity


@override_settings(ALLOWED_HOSTS=["testserver"])
class UnifiedMouseEditorTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username="unified-mice"))
        self.protocol = Protocol.objects.create(protocol_number=67, licence_reference="UNIFIED", name="Unified")
        self.mice = [Mouse.objects.create(mouse_id=name, protocol=self.protocol, status="Alive", notes=name)
                     for name in ("First / mouse", "Second mouse")]
        self.kind = ProcedureType.objects.create(name="Unified procedure")
        species = Species.objects.create(name="Unified species")
        self.line = MouseLine.objects.create(name="Unified gene", species=species)
        self.crossing, _ = resolve_crossing(species, [self.line])
        self.url = reverse("lab:edit_selected_mice_all")
        self.scoped = reverse("lab:edit_selected_mice_licence_protocol", args=["UNIFIED", 67])
        self.add_url = reverse("lab:add_litter_all")

    def payload(self, renames, **extra):
        return {"selected_mouse": [mouse.pk for mouse in self.mice], "apply_bulk_edit": "1",
                "confirm_mouse_ids": "on", **{individual_mouse_field_prefix(mouse) + "_mouse_id": renames.get(mouse.pk, mouse.pk)
                                                for mouse in self.mice}, **extra}

    def test_swapping_names_preserves_each_animals_genetics_procedures_and_edits(self):
        calls, procedures = [], []
        for index, mouse in enumerate(self.mice):
            sync_mouse_structured_genetics(mouse, self.crossing, {self.line.pk: "het" if index == 0 else "wt"},
                                          uncertainty_values={self.line.pk: index == 0})
            calls.append(mouse.genotype_calls.get())
            procedures.append(Procedure.objects.create(mouse=mouse, procedure_type=self.kind, notes=mouse.pk))
        old_ids = [mouse.pk for mouse in self.mice]
        response = self.client.post(self.scoped, self.payload(dict(zip(old_ids, reversed(old_ids))),
            update_status="on", status="Culled", **{individual_mouse_field_prefix(self.mice[0]) + "_local_identifier": "007"}))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(parse_qs(urlsplit(response["Location"]).query)["selected_mouse"], old_ids[::-1])
        for index, target in enumerate(old_ids[::-1]):
            mouse = Mouse.objects.get(pk=target)
            self.assertEqual((mouse.notes, mouse.status, mouse.id_status), (old_ids[index], "Culled", Mouse.ID_ASSIGNED))
            calls[index].refresh_from_db()
            procedures[index].refresh_from_db()
            self.assertEqual(calls[index].mouse_id, target)
            self.assertEqual(calls[index].is_uncertain, index == 0)
            self.assertEqual((procedures[index].mouse_id, procedures[index].notes), (target, old_ids[index]))
        self.assertEqual(Mouse.objects.get(pk=old_ids[1]).tattoo, "007")
        self.assertEqual(Mouse.objects.count(), 2)

    def test_invalid_batch_names_and_missing_confirmation_never_write(self):
        outsider = Mouse.objects.create(mouse_id="Taken", protocol=self.protocol)
        first, second = self.mice
        cases = [({first.pk: outsider.pk}, {}), ({first.pk: second.pk}, {}),
                 ({first.pk: "Same", second.pk: "Same"}, {}), ({first.pk: " "}, {}),
                 ({first.pk: "X" * 101}, {}), ({first.pk: "New"}, {"confirm_mouse_ids": ""})]
        for renames, extra in cases:
            with self.subTest(renames=renames):
                response = self.client.post(self.url, self.payload(renames, update_status="on", status="Culled", **extra))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.assertEqual(list(Mouse.objects.filter(pk__in=[m.pk for m in self.mice]).values_list("status", flat=True)), ["Alive", "Alive"])
        self.assertEqual(Mouse.objects.count(), 3)

    def test_failure_during_final_rename_rolls_back_entire_batch_and_details(self):
        first, second = self.mice
        procedures = [Procedure.objects.create(mouse=mouse, procedure_type=self.kind) for mouse in self.mice]
        def conflict(old, new):
            if new == "New second":
                raise IntegrityError("Concurrent ID conflict")
            return rename_mouse_identity(old, new)
        with patch("lab.views.mice.rename_mouse_identity", side_effect=conflict):
            response = self.client.post(self.url, self.payload({first.pk: "New first", second.pk: "New second"},
                update_status="on", status="Culled"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No changes were saved")
        self.assertEqual(set(Mouse.objects.values_list("pk", flat=True)), {first.pk, second.pk})
        for mouse, procedure in zip(self.mice, procedures):
            mouse.refresh_from_db()
            procedure.refresh_from_db()
            self.assertEqual((mouse.status, mouse.id_status, procedure.mouse_id), ("Alive", Mouse.ID_UNKNOWN, mouse.pk))

    def test_new_procedure_action_does_not_apply_draft_names_or_mouse_details(self):
        first, second = self.mice
        data = self.payload({first.pk: "Unsaved name"}, update_status="on", status="Unsaved status")
        data.update(create_procedures="1", **{"new_procedure-procedure_type": self.kind.pk})
        response = self.client.post(self.url, data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Procedure.objects.count(), 2)
        self.assertEqual(set(Procedure.objects.values_list("mouse_id", flat=True)), {first.pk, second.pk})
        first.refresh_from_db()
        self.assertEqual(first.status, "Alive")
        self.assertFalse(Mouse.objects.filter(pk="Unsaved name").exists())

    def test_prefix_only_creates_one_arbitrary_id_and_embeds_individual_fields(self):
        data = {"mouse_id_prefix": "Arbitrary / name", "mouse_numbers": "", "protocol": self.protocol.pk,
                "pup__sex": "F", "pup__marking": "L/R", "crossing_definition": self.crossing.pk,
                f"pup__genotype_line_{self.line.pk}": "hom"}
        preview = self.client.post(self.add_url, {**data, "preview_mice": "1"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(preview.json()["mouse_count"], 1)
        self.assertFalse(Mouse.objects.filter(pk=data["mouse_id_prefix"]).exists())
        rendered = self.client.post(self.add_url, {**data, "preview_mice": "1"})
        html = Markup(rendered.content.decode())
        self.assertTrue(html.find("input", name="pup__marking")[0].within("div", id="mouse-panel-shared"))
        self.assertIn("hidden", html.find("button", id="mouse-tab-individual")[0].attrs)
        response = self.client.post(self.add_url, data)
        self.assertEqual(response.status_code, 302)
        mouse = Mouse.objects.get(pk=data["mouse_id_prefix"])
        self.assertEqual((mouse.sex, mouse.tattoo, mouse.id_status), ("F", "L/R", Mouse.ID_ASSIGNED))
        self.assertEqual(mouse.genotype_calls.get().zygosity, "hom")

    def test_range_creation_exposes_individual_tab_and_reports_count_without_writes(self):
        data = {"mouse_id_prefix": "Range-", "mouse_numbers": "01,03-04", "protocol": self.protocol.pk, "preview_mice": "1"}
        response = self.client.post(self.add_url, data)
        html = Markup(response.content.decode())
        tabs = html.find("button", role="tab")
        self.assertEqual(tabs[0].attrs["data-mouse-tab"], "ids")
        self.assertNotIn("hidden", html.find("button", id="mouse-tab-individual")[0].attrs)
        self.assertTrue(html.find("input", name="pup_01_marking")[0].within("section", id="mouse-panel-individual"))
        ajax = self.client.post(self.add_url, data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(ajax.json()["mouse_count"], 3)
        self.assertFalse(Mouse.objects.filter(pk__startswith="Range-").exists())

    def test_local_id_type_can_be_changed_for_one_mouse(self):
        mouse = self.mice[0]
        response = self.client.post(self.url, {"selected_mouse": [mouse.pk], "apply_bulk_edit": "1",
            "update_marking_type": "on", "marking_type": "ear_clip"})
        self.assertEqual(response.status_code, 302)
        mouse.refresh_from_db()
        self.assertEqual(mouse.marking_type, "ear_clip")
