import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase

from flare_ai.context import AIRequestContext, PageContext
from flare_ai.schemas import AnimalDraftRow, AnimalFormDraftInput
from flare_ai.tools.animal_forms import prepare_animal_form
from lab.forms import (
    ANIMAL_FORM_DRAFT_LIMIT,
    ANIMAL_FORM_DRAFT_MAX_AGE,
    ANIMAL_FORM_DRAFT_SESSION_KEY,
    genotype_field_name,
)
from lab.models import GenotypeCategory, Mouse, MouseLine, Protocol, Species, resolve_crossing


class AnimalFormDraftTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="animal-draft-user")
        self.client.force_login(self.user)
        self.protocol = Protocol.objects.create(
            protocol_number=41,
            licence_reference="AI-LICENCE",
            name="AI protocol",
        )
        species = Species.objects.create(name="AI draft species")
        self.line = MouseLine.objects.create(species=species, name="Draft gene", aliases="DG")
        self.crossing, _ = resolve_crossing(species, [self.line])
        for code, label, position in (
            ("unknown", "Unknown", 0),
            ("wt", "Wild type", 1),
            ("het", "Heterozygous", 2),
            ("hom", "Homozygous", 3),
        ):
            GenotypeCategory.objects.update_or_create(
                code=code,
                defaults={"label": label, "position": position, "active": True},
            )

    def context(self, path="/mice/"):
        session = self.client.session
        deps = AIRequestContext(
            user=self.user,
            page=PageContext(route="mice_page", path=path),
            session=session,
        )
        return SimpleNamespace(deps=deps), session

    def input(self, **changes):
        values = {
            "protocol_id": self.protocol.pk,
            "crossing_id": self.crossing.pk,
            "mouse_id_prefix": "AI-",
            "mouse_numbers": "1-2",
            "date_of_birth": "2026-11-12",
            "breeding_pair": "Pair 7",
        }
        values.update(changes)
        return AnimalFormDraftInput(**values)

    def prepare(self, draft):
        context, session = self.context()
        result = prepare_animal_form(context, draft)
        session.save()
        return result

    def test_prepares_existing_form_without_creating_animals_and_normal_save_creates(self):
        parent = Mouse.objects.create(mouse_id="DAM-1", protocol=self.protocol)
        result = self.prepare(self.input(
            mother_id=parent.pk,
            shared_genotypes={"DG": "Heterozygous"},
            animals=[
                AnimalDraftRow(number="1", local_identifier="left", sex="F"),
                AnimalDraftRow(
                    number="2", local_identifier="right", sex="M",
                    genotypes={"Draft gene": "Homozygous"},
                ),
            ],
        ))

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["summary"]["animal_count"], 2)
        self.assertEqual(Mouse.objects.count(), 1)

        response = self.client.get(result["url"])
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertTrue(form.is_bound)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form["protocol"].value(), str(self.protocol.pk))
        self.assertEqual(form["mother_external_id"].value(), parent.pk)
        self.assertContains(response, "AI-prepared draft loaded")
        self.assertEqual(Mouse.objects.count(), 1)

        saved = self.client.post(result["url"], data=form.data)
        self.assertEqual(saved.status_code, 302)
        first, second = Mouse.objects.get(pk="AI-1"), Mouse.objects.get(pk="AI-2")
        self.assertEqual((first.mother_id, first.tattoo, first.sex), (parent.pk, "left", "F"))
        self.assertEqual((second.mother_id, second.tattoo, second.sex), (parent.pk, "right", "M"))
        field = genotype_field_name(self.line)
        self.assertEqual(form[f"pup_1_{field}"].value(), "het")
        self.assertEqual(first.genotype_calls.get(mouse_line=self.line).zygosity, "het")
        self.assertEqual(second.genotype_calls.get(mouse_line=self.line).zygosity, "hom")

    def test_unmatched_parent_requires_confirmation_then_stays_external(self):
        draft = self.input(father_id="UNKNOWN-SIRE")
        context, session = self.context()
        result = prepare_animal_form(context, draft)
        self.assertEqual(result["status"], "needs_input")
        self.assertIn("not found", result["message"])
        self.assertNotIn(ANIMAL_FORM_DRAFT_SESSION_KEY, session)

        result = self.prepare(self.input(
            father_id="UNKNOWN-SIRE",
            keep_unlinked_father=True,
        ))
        response = self.client.get(result["url"])
        form = response.context["form"]
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["shared_parentage"]["father_external_id"], "UNKNOWN-SIRE")
        self.assertIsNone(form.cleaned_data["shared_parentage"]["father_id"])

    def test_required_ambiguity_and_invalid_identity_do_not_create_draft(self):
        Protocol.objects.create(
            protocol_number=self.protocol.protocol_number,
            licence_reference="OTHER-LICENCE",
            name="Other protocol",
        )
        context, session = self.context()
        result = prepare_animal_form(context, AnimalFormDraftInput(
            protocol_number=self.protocol.protocol_number,
            crossing_id=self.crossing.pk,
            mouse_id_prefix="AI-1",
        ))
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(len(result["protocol_candidates"]), 2)
        self.assertNotIn(ANIMAL_FORM_DRAFT_SESSION_KEY, session)

        result = prepare_animal_form(context, self.input(mouse_id_prefix="", mouse_numbers=""))
        self.assertEqual(result["status"], "needs_input")
        self.assertNotIn(ANIMAL_FORM_DRAFT_SESSION_KEY, session)

    def test_expired_or_other_user_draft_is_not_loaded(self):
        result = self.prepare(self.input())
        draft_id = parse_qs(urlsplit(result["url"]).query)["ai_draft"][0]
        session = self.client.session
        drafts = session[ANIMAL_FORM_DRAFT_SESSION_KEY]
        drafts[draft_id]["created_at"] = time.time() - ANIMAL_FORM_DRAFT_MAX_AGE - 1
        session[ANIMAL_FORM_DRAFT_SESSION_KEY] = drafts
        session.save()

        response = self.client.get(result["url"])
        self.assertContains(response, "missing or expired")
        self.assertFalse(response.context["form"].is_bound)

    def test_generated_ids_optional_unknowns_and_session_limit_reuse_existing_rules(self):
        draft = self.input(
            generate_ids=True,
            mouse_id_prefix="",
            mouse_numbers="03",
            animals=[AnimalDraftRow(number="03", sex="unknown")],
        )
        result = self.prepare(draft)
        response = self.client.get(result["url"])
        form = response.context["form"]
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form["mouse_id_prefix"].value().startswith("261112_"))
        self.assertEqual(form["pup_03_sex"].value(), "")
        self.assertEqual(form[f"pup_03_{genotype_field_name(self.line)}"].value(), "unknown")

        for index in range(ANIMAL_FORM_DRAFT_LIMIT + 1):
            result = self.prepare(self.input(mouse_id_prefix=f"LIMIT-{index}-"))
            self.assertEqual(result["status"], "ready")
        self.assertEqual(
            len(self.client.session[ANIMAL_FORM_DRAFT_SESSION_KEY]),
            ANIMAL_FORM_DRAFT_LIMIT,
        )

        result = self.prepare(self.input(mouse_id_prefix="OTHER-"))
        draft_id = parse_qs(urlsplit(result["url"]).query)["ai_draft"][0]
        session = self.client.session
        drafts = session[ANIMAL_FORM_DRAFT_SESSION_KEY]
        drafts[draft_id]["user_id"] = "someone-else"
        session[ANIMAL_FORM_DRAFT_SESSION_KEY] = drafts
        session.save()
        response = self.client.get(result["url"])
        self.assertContains(response, "missing or expired")
        self.assertFalse(response.context["form"].is_bound)
