import json
from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse

from .forms import LitterCreateForm
from .models import Mouse, Procedure, ProcedureType, Protocol, Species, MouseLine, resolve_crossing
from .test_navigation import Markup


@override_settings(ALLOWED_HOSTS=["testserver"])
class LitterProcedureDraftTests(TestCase):
    def setUp(self):
        self.protocol = Protocol.objects.create(protocol_number=1, licence_reference="DRAFTS", name="Drafts")
        species = Species.objects.create(name="Draft species")
        line = MouseLine.objects.create(species=species, name="Draft gene")
        self.crossing, _ = resolve_crossing(species, [line])
        self.types = [ProcedureType.objects.create(name=name) for name in ("Injection", "Observation")]
        self.client.force_login(get_user_model().objects.create_user(username="draft-user"))
        self.url = reverse("lab:add_litter_all")

    def payload(self, drafts, **extra):
        return {"mouse_id_prefix": "Draft-", "mouse_numbers": "1-2", "protocol": self.protocol.pk,
                "crossing_definition": self.crossing.pk, "procedure_drafts": json.dumps(drafts), **extra}

    def test_multiple_and_repeated_types_create_separate_records_per_mouse(self):
        drafts = [
            {"procedure_type": self.types[0].pk, "date": "2026-10-21", "lab_member": "AB", "lab_book_page": "001"},
            {"procedure_type": self.types[1].pk},
            {"procedure_type": self.types[0].pk, "date": "", "lab_member": "CD"},
        ]
        response = self.client.post(self.url, self.payload(drafts))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Mouse.objects.count(), 2)
        self.assertEqual(Procedure.objects.count(), 6)
        for mouse in Mouse.objects.all():
            records = list(mouse.procedures.order_by("pk"))
            self.assertEqual([record.procedure_type_id for record in records], [draft["procedure_type"] for draft in drafts])
            self.assertEqual(str(records[0].date), "2026-10-21")
            self.assertEqual(records[0].lab_book_page, "001")
            self.assertIsNone(records[2].date)
            self.assertEqual(records[2].lab_member, "CD")

    def test_invalid_draft_rejects_whole_litter_and_preserves_values(self):
        valid = {"procedure_type": self.types[0].pk}
        invalids = [None, {}, {"procedure_type": 999999}, {**valid, "date": "bad"},
                    {**valid, "lab_member": "x" * 101}, {**valid, "date": []}]
        for invalid in invalids:
            with self.subTest(invalid=invalid):
                response = self.client.post(self.url, self.payload([valid, invalid], pup_1_marking="L2R1"))
                self.assertEqual(response.status_code, 200)
                self.assertIn("procedure_drafts", response.context["form"].errors)
                self.assertEqual(json.loads(response.context["form"]["procedure_drafts"].value()), [valid, invalid])
                self.assertContains(response, "L2R1")
                self.assertFalse(Mouse.objects.exists())
                self.assertFalse(Procedure.objects.exists())

    def test_malformed_json_and_non_lists_are_rejected(self):
        for raw in ("{", '"text"', '{"procedure_type": 1}', '[1]'):
            form = LitterCreateForm(self.payload([], procedure_drafts=raw))
            self.assertFalse(form.is_valid())
            self.assertIn("procedure_drafts", form.errors)

    def test_restricted_protocol_rejects_queue_but_allows_empty_queue(self):
        self.protocol.allows_regulated_procedures = False
        self.protocol.save()
        response = self.client.post(self.url, self.payload([{"procedure_type": self.types[0].pk}]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("procedure_drafts", response.context["form"].errors)
        self.assertFalse(Mouse.objects.exists())
        self.assertEqual(self.client.post(self.url, self.payload([])).status_code, 302)
        self.assertFalse(Procedure.objects.exists())

    def test_unadded_inputs_do_not_create_procedures(self):
        payload = self.payload([], procedure_type=self.types[0].pk)
        payload["new_procedure-procedure_type"] = self.types[1].pk
        self.assertEqual(self.client.post(self.url, payload).status_code, 302)
        self.assertEqual(Mouse.objects.count(), 2)
        self.assertFalse(Procedure.objects.exists())

    def test_row_preview_writes_nothing_and_does_not_consume_drafts(self):
        payload = self.payload([{"procedure_type": self.types[0].pk}], preview_mice="1")
        response = self.client.post(self.url, payload, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        self.assertIn("html", response.json())
        self.assertFalse(Mouse.objects.exists())
        self.assertFalse(Procedure.objects.exists())

    def test_failure_during_creation_rolls_back_mice_and_all_procedures(self):
        original = Procedure.objects.create
        calls = 0

        def create(**values):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise IntegrityError("Trial failure")
            return original(**values)

        with patch("lab.views.mice.Procedure.objects.create", side_effect=create):
            response = self.client.post(self.url, self.payload([{"procedure_type": item.pk} for item in self.types]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Mouse.objects.exists())
        self.assertFalse(Procedure.objects.exists())

    def test_both_routes_have_independent_draft_form_and_main_hidden_queue(self):
        scoped = reverse("lab:add_litter_licence_protocol", kwargs={"licence_reference": self.protocol.licence_reference, "protocol_number": 1})
        for url in (self.url, scoped):
            response = self.client.get(url)
            markup = Markup(response.content.decode())
            queue = markup.find("input", name="procedure_drafts")[0]
            self.assertTrue(queue.within("form", id="primary-form"))
            self.assertEqual(json.loads(queue.attrs["value"]), [])
            draft_form = markup.find("form", id="litter-procedure-form")[0]
            self.assertFalse(draft_form.within("form", id="primary-form"))
            self.assertContains(response, "Procedures for this litter")
