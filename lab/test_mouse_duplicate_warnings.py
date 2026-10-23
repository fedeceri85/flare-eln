from datetime import date
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import LitterCreateForm, MouseEditForm, MouseForm, genotype_field_name, sync_mouse_structured_genetics, individual_mouse_field_prefix
from .models import Experiment, Mouse, MouseLine, Procedure, ProcedureType, Project, Protocol, Recording, RecordingType, Species, resolve_crossing
from .views import create_litter_mice, possible_duplicate_mouse_warning, rename_mouse_identity


@override_settings(ALLOWED_HOSTS=["testserver"])
class MouseDuplicateWarningTests(TestCase):
    def setUp(self):
        self.protocol = Protocol.objects.create(protocol_number=1, licence_reference="DUPLICATES", name="Test")
        self.other_protocol = Protocol.objects.create(protocol_number=2, licence_reference="OTHER", name="Other protocol")
        species = Species.objects.create(name="Duplicate test species")
        self.line = MouseLine.objects.create(species=species, name="Gene A")
        self.crossing, _ = resolve_crossing(species, [self.line])
        self.dob = date(2026, 6, 10)
        self.user = get_user_model().objects.create_user(username="duplicate-test")
        self.client.force_login(self.user)
        self.add_url = reverse("lab:add_litter_all")
        self.litter_url = reverse("lab:add_litter_all")

    def payload(self, **extra):
        data = {
            "mouse_id": "NEW-ASSIGNED", "date_of_birth": self.dob.isoformat(), "breeding_pair": "",
            "crossing_definition": self.crossing.pk, "protocol": self.protocol.pk, "status": "Alive",
            "marking_type": "other", "sex": "F", "custom_metadata": "{}",
            genotype_field_name(self.line): "het", **extra,
        }
        data.setdefault("mouse_id_prefix", data["mouse_id"])
        data.setdefault("pup__sex", data["sex"])
        if "tattoo" in data:
            data.setdefault("pup__marking", data["tattoo"])
        if data.get("generate_mouse_id"):
            data.update(no_id_available="on", mouse_numbers="1", pup_1_marking=data.get("tattoo", ""), pup_1_sex=data["sex"])
        return data

    def candidate(self, mouse_id="EXISTING-PUP", **extra):
        return Mouse.objects.create(
            mouse_id=mouse_id, date_of_birth=self.dob, crossing_definition=self.crossing,
            protocol=extra.pop("protocol", self.protocol), id_status=extra.pop("id_status", Mouse.ID_TEMPORARY),
            **extra,
        )

    def warning(self, **extra):
        form = MouseForm(self.payload(**extra))
        self.assertTrue(form.is_valid(), form.errors)
        return possible_duplicate_mouse_warning(form.cleaned_data)

    def confirm_payload(self, response, payload):
        warning = response.context["possible_duplicate_warning"]
        self.assertIsNotNone(warning)
        return {**payload, "confirm_possible_duplicate": "1", "possible_duplicate_signature": warning["signature"]}

    def test_temporary_id_matches_without_breeding_pair_even_if_it_looks_numeric(self):
        mouse = self.candidate("12345", breeding_pair="Different pair", protocol=self.other_protocol)
        warning = self.warning()
        self.assertEqual(warning["mice"], [mouse])
        self.assertFalse(warning["rows"][0]["breeding_pair_matches"])

    def test_legacy_unknown_status_matches_without_guessing_from_name(self):
        mouse = self.candidate("MANUAL-LOOKING-ID", id_status=Mouse.ID_UNKNOWN)
        self.assertEqual(self.warning()["mice"], [mouse])

    def test_assigned_id_with_generated_looking_name_is_not_assumed_temporary(self):
        self.candidate("20260610_gene-a_pair-1_1", id_status=Mouse.ID_ASSIGNED)
        self.assertIsNone(self.warning())

    def test_same_pair_does_not_warn_for_assigned_ids(self):
        mouse = self.candidate(id_status=Mouse.ID_ASSIGNED, breeding_pair="Pair A")
        self.assertIsNone(self.warning(breeding_pair="pair a"))

    def test_same_local_identifier_does_not_warn_for_an_assigned_mouse(self):
        mouse = self.candidate(id_status=Mouse.ID_ASSIGNED, tattoo="L2R1", marking_type="ear_clip", breeding_pair="Other pair")
        warning = self.warning(tattoo="l2r1", marking_type="ear_clip")
        self.assertIsNone(warning)

    def test_different_birth_date_or_crossing_does_not_match(self):
        mouse = self.candidate()
        mouse.date_of_birth = date(2026, 6, 11)
        mouse.save()
        self.assertIsNone(self.warning())
        mouse.date_of_birth = self.dob
        other_line = MouseLine.objects.create(species=self.line.species, name="Different gene")
        mouse.crossing_definition, _ = resolve_crossing(self.line.species, [other_line])
        mouse.save()
        self.assertIsNone(self.warning())

    def test_missing_birth_date_or_crossing_skips_lookup(self):
        with self.assertNumQueries(0):
            self.assertIsNone(possible_duplicate_mouse_warning({"date_of_birth": None, "crossing_definition": self.crossing}))
            self.assertIsNone(possible_duplicate_mouse_warning({"date_of_birth": self.dob, "crossing_definition": None}))

    def test_warning_ranks_and_highlights_matching_details(self):
        for index in range(11):
            self.candidate(f"WEAK-{index}")
        strong = self.candidate("STRONG", breeding_pair="Pair A", tattoo="07", marking_type="tattoo", sex="F")
        warning = self.warning(breeding_pair="Pair A", tattoo="07", marking_type="other")
        self.assertEqual(warning["mice"][0], strong)
        self.assertTrue(warning["rows"][0]["breeding_pair_matches"])
        self.assertTrue(warning["rows"][0]["sex_matches"])
        self.assertFalse(warning["rows"][0]["local_identifier_matches"])
        self.assertEqual((warning["count"], len(warning["mice"]), warning["more_count"]), (12, 10, 2))

    def test_readding_temporary_pup_warns_and_requires_explicit_save_anyway(self):
        existing = self.candidate(protocol=self.other_protocol, tattoo="L2R1", sex="F")
        payload = self.payload()
        response = self.client.post(self.add_url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, existing.pk)
        self.assertContains(response, "Save as new animal anyway")
        self.assertContains(response, "Assign permanent ID")
        self.assertContains(response, "Other protocol")
        self.assertNotRegex(response.content.decode(), r'<input[^>]*name="confirm_possible_duplicate"')
        self.assertEqual(Mouse.objects.count(), 1)
        repeated = self.client.post(self.add_url, payload)
        self.assertEqual(repeated.status_code, 200)
        saved = self.client.post(self.add_url, self.confirm_payload(response, payload))
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(Mouse.objects.get(pk="NEW-ASSIGNED").id_status, Mouse.ID_ASSIGNED)
        self.assertEqual(Mouse.objects.count(), 2)

    def test_tampered_or_missing_confirmation_token_cannot_bypass_warning(self):
        self.candidate()
        for token in ("", "not-a-valid-token"):
            response = self.client.post(self.add_url, self.payload(confirm_possible_duplicate="1", possible_duplicate_signature=token))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(Mouse.objects.filter(pk="NEW-ASSIGNED").exists())

    def test_changed_input_requires_a_new_confirmation(self):
        self.candidate()
        original = self.payload()
        response = self.client.post(self.add_url, original)
        confirmed = self.confirm_payload(response, original)
        for change in ({"mouse_id_prefix": "DIFFERENT-ID"}, {"breeding_pair": "New pair"}, {"pup__sex": "M"}, {"notes": "Changed"}):
            updated = self.client.post(self.add_url, {**confirmed, **change})
            self.assertEqual(updated.status_code, 200)
            self.assertEqual(Mouse.objects.count(), 1)

    def test_new_candidate_since_warning_requires_review_again(self):
        self.candidate()
        payload = self.payload()
        response = self.client.post(self.add_url, payload)
        self.candidate("ADDED-LATER")
        updated = self.client.post(self.add_url, self.confirm_payload(response, payload))
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.context["possible_duplicate_warning"]["count"], 2)
        self.assertFalse(Mouse.objects.filter(pk="NEW-ASSIGNED").exists())

    def test_automatic_single_creation_records_temporary_status(self):
        response = self.client.post(self.add_url, self.payload(
            mouse_id="", generate_mouse_id="on", breeding_pair="Pair 1", tattoo="07", marking_type="tattoo",
        ))
        self.assertEqual(response.status_code, 302)
        mouse = Mouse.objects.get()
        self.assertEqual(mouse.id_status, Mouse.ID_TEMPORARY)

    def test_litter_creation_records_temporary_and_assigned_status(self):
        for generated, prefix in ((True, ""), (False, "ASSIGNED-")):
            form = LitterCreateForm(self.payload(
                mouse_numbers="1-2", mouse_id_prefix=prefix, no_id_available="on" if generated else "",
                breeding_pair="Pair 1",
            ))
            self.assertTrue(form.is_valid(), form.errors)
            mice, _ = create_litter_mice(self.protocol, form)
            self.assertTrue(all(mouse.id_status == (Mouse.ID_TEMPORARY if generated else Mouse.ID_ASSIGNED) for mouse in mice))

    def test_litter_warning_and_explicit_confirmation_create_whole_batch(self):
        self.candidate()
        payload = self.payload(mouse_numbers="1-2", mouse_id_prefix="LITTER-")
        response = self.client.post(self.litter_url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Save as new animal anyway")
        self.assertEqual(Mouse.objects.count(), 1)
        saved = self.client.post(self.litter_url, self.confirm_payload(response, payload))
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(set(Mouse.objects.exclude(pk="EXISTING-PUP").values_list("pk", flat=True)), {"LITTER-1", "LITTER-2"})

    def test_typed_id_matching_temporary_local_identifier_can_be_saved_after_warning(self):
        mouse = self.candidate(tattoo="07", marking_type="tattoo", breeding_pair="Pair 1")
        payload = self.payload(tattoo="07", marking_type="tattoo", breeding_pair="Pair 1")
        response = self.client.post(self.add_url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["form"].errors)
        self.assertContains(response, "Assign permanent ID")
        self.assertContains(response, mouse.pk)
        saved = self.client.post(self.add_url, self.confirm_payload(response, payload))
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(Mouse.objects.count(), 2)

    def test_distinct_assigned_id_saves_despite_matching_all_animal_details(self):
        self.candidate(id_status=Mouse.ID_ASSIGNED, tattoo="07", marking_type="tattoo", breeding_pair="Pair 1", sex="F")
        response = self.client.post(self.add_url, self.payload(tattoo="07", marking_type="tattoo", breeding_pair="Pair 1"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Mouse.objects.get(pk="NEW-ASSIGNED").id_status, Mouse.ID_ASSIGNED)
        self.assertEqual(Mouse.objects.count(), 2)

    def test_exact_assigned_mouse_id_still_cannot_be_duplicated(self):
        self.candidate("NEW-ASSIGNED", id_status=Mouse.ID_ASSIGNED)
        response = self.client.post(self.add_url, self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertIn("mouse_numbers", response.context["form"].errors)
        self.assertIsNone(response.context["possible_duplicate_warning"])
        self.assertEqual(Mouse.objects.count(), 1)

    def test_assigned_matches_are_excluded_when_unresolved_matches_also_exist(self):
        assigned = self.candidate("ASSIGNED", id_status=Mouse.ID_ASSIGNED, breeding_pair="Pair A")
        temporary = self.candidate("TEMPORARY", breeding_pair="Pair A")
        unknown = self.candidate("UNKNOWN", id_status=Mouse.ID_UNKNOWN, breeding_pair="Pair A")
        self.assertEqual(set(self.warning(breeding_pair="Pair A")["mice"]), {temporary, unknown})
        self.assertNotIn(assigned, self.warning(breeding_pair="Pair A")["mice"])

    def test_warning_links_to_prefilled_permanent_id_form(self):
        mouse = self.candidate()
        response = self.client.post(self.add_url, self.payload(mouse_id="12345"))
        link = response.context["possible_duplicate_warning"]["rows"][0]["rename_url"]
        self.assertEqual(urlsplit(link).path, reverse("lab:rename_mouse", args=[mouse.pk]))
        self.assertEqual(parse_qs(urlsplit(link).query)["new_mouse_id"], ["12345"])
        rename_page = self.client.get(link, follow=True)
        self.assertEqual(rename_page.context["form"][f"{individual_mouse_field_prefix(mouse)}_mouse_id"].value(), "12345")
        self.assertFalse(rename_page.context["form"]["confirm_mouse_ids"].value())

    def test_assigning_permanent_id_preserves_marking_and_all_linked_records(self):
        mouse = self.candidate(tattoo="07", marking_type="tattoo", breeding_pair="Pair 1", notes="Original history")
        sync_mouse_structured_genetics(mouse, self.crossing, {self.line.pk: "het"}, uncertainty_values={self.line.pk: True})
        call = mouse.genotype_calls.get()
        procedure = Procedure.objects.create(mouse=mouse, procedure_type=ProcedureType.objects.create(name="Test procedure"))
        kind = RecordingType.objects.get(slug="imaging")
        experiment = Experiment.objects.create(project=Project.objects.create(name="Test project"), name="Test", recording_type=kind)
        recording = Recording.objects.create(experiment=experiment, recording_type=kind, owner=self.user, recording_date=self.dob)
        recording.mice.add(mouse)
        response = self.client.post(reverse("lab:edit_selected_mice_all"), {
            "selected_mouse": [mouse.pk], "apply_bulk_edit": "1",
            f"{individual_mouse_field_prefix(mouse)}_mouse_id": "12345", "confirm_mouse_ids": "on",
        })
        self.assertEqual(response.status_code, 302)
        renamed = Mouse.objects.get(pk="12345")
        self.assertEqual((renamed.id_status, renamed.tattoo, renamed.notes), (Mouse.ID_ASSIGNED, "07", "Original history"))
        self.assertFalse(Mouse.objects.filter(pk=mouse.pk).exists())
        call.refresh_from_db()
        procedure.refresh_from_db()
        self.assertEqual(call.mouse_id, renamed.pk)
        self.assertTrue(call.is_uncertain)
        self.assertEqual(procedure.mouse_id, renamed.pk)
        self.assertEqual(list(recording.mice.values_list("pk", flat=True)), [renamed.pk])

    def test_failed_rename_restores_original_marking_and_status(self):
        mouse = self.candidate(tattoo="07", marking_type="tattoo", breeding_pair="Pair 1")
        Mouse.objects.create(mouse_id="TAKEN", protocol=self.protocol)
        with self.assertRaises(IntegrityError):
            rename_mouse_identity(mouse.pk, "TAKEN")
        mouse.refresh_from_db()
        self.assertEqual((mouse.tattoo, mouse.id_status), ("07", Mouse.ID_TEMPORARY))

    def test_ordinary_edit_keeps_temporary_status(self):
        mouse = self.candidate()
        form = MouseEditForm(self.payload(mouse_id=mouse.pk, notes="Edited"), instance=mouse)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        mouse.refresh_from_db()
        self.assertEqual(mouse.id_status, Mouse.ID_TEMPORARY)

    def test_unknown_is_default_for_records_without_provenance(self):
        mouse = Mouse.objects.create(mouse_id="IMPORTED-123", protocol=self.protocol)
        self.assertEqual(mouse.id_status, Mouse.ID_UNKNOWN)
        self.assertNotIn("id_status", MouseEditForm(instance=mouse).fields)

    def test_unresolved_records_still_reject_identical_nonblank_local_identifiers(self):
        self.candidate(tattoo="07", marking_type="tattoo", breeding_pair="Pair 1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.candidate("OTHER-UNKNOWN", id_status=Mouse.ID_UNKNOWN, tattoo="07", marking_type="tattoo", breeding_pair="Pair 1")

    def test_assigned_litter_can_share_details_with_other_assigned_ids(self):
        self.candidate(id_status=Mouse.ID_ASSIGNED, tattoo="07", marking_type="tattoo", breeding_pair="Pair 1")
        response = self.client.post(self.litter_url, self.payload(
            mouse_numbers="1-2", mouse_id_prefix="TYPED-", breeding_pair="Pair 1", marking_type="tattoo",
            pup_1_marking="07", pup_2_marking="07",
        ))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Mouse.objects.count(), 3)
        self.assertEqual(set(Mouse.objects.values_list("id_status", flat=True)), {Mouse.ID_ASSIGNED})

    def test_automatic_litter_cannot_bypass_unresolved_identifier_uniqueness(self):
        self.candidate(tattoo="07", marking_type="tattoo", breeding_pair="Pair 1")
        payload = self.payload(mouse_numbers="1-2", no_id_available="on", breeding_pair="Pair 1",
                               marking_type="tattoo", pup_1_marking="07", pup_2_marking="08")
        warning = self.client.post(self.litter_url, payload)
        self.assertEqual(warning.status_code, 200)
        response = self.client.post(self.litter_url, self.confirm_payload(warning, payload))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertEqual(Mouse.objects.count(), 1)
