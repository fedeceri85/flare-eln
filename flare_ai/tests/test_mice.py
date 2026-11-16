import json
from datetime import date

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from pydantic import ValidationError

from flare_ai.schemas import MouseSearchInput
from flare_ai.tools.mice import FILTER_LOOKUPS, filter_mice, search_mice
from lab.models import GenotypeCategory, Mouse, MouseGenotype, Protocol
from lab.tests._support import create_test_crossing


class MouseSearchInputTests(SimpleTestCase):
    def test_non_date_conditions_and_false_boolean_are_valid(self):
        for fields in ({"status": "Alive"}, {"severity_exceeded": False},
                       {"genotypes": [{"uncertain": False}]}, {"missing": ["notes"]}):
            with self.subTest(fields=fields):
                MouseSearchInput(**fields)

    def test_empty_conditions_and_unknown_lookups_are_rejected(self):
        for fields in ({}, {"genotypes": []}, {"missing": []}, {"genotypes": [{}]},
                       {"status": "Dead"}, {"sex": "female"}, {"notes": "  "},
                       {"mouse_id__regex": ".*"}, {"missing": ["protocol__name"]},
                       {"custom_metadata": {}}, {"custom_metadata": {" ": 1}},
                       {"missing": ["notes"], "present": ["notes"]}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                MouseSearchInput(**fields)

    def test_all_date_ranges_and_exact_dates_are_checked(self):
        for exact, lower, upper in (
            ("date_of_birth", "born_from", "born_to"),
            ("date_added", "added_from", "added_to"),
            ("protocol_start_date", "protocol_start_from", "protocol_start_to"),
            ("cull_date", "culled_from", "culled_to"),
        ):
            with self.subTest(date=exact):
                query = MouseSearchInput(**{lower: "2026-10-01", upper: "2026-10-01"})
                self.assertEqual(getattr(query, lower), date(2026, 10, 1))
                for fields in ({lower: "2026-10-02", upper: "2026-10-01"},
                               {exact: "2026-09-30", lower: "2026-10-01"},
                               {exact: "2026-10-02", upper: "2026-10-01"},
                               {exact: "not-a-date"}):
                    with self.assertRaises(ValidationError):
                        MouseSearchInput(**fields)

    def test_every_input_has_an_implemented_filter(self):
        special = {"crossing", "crossing_type", "genotypes", "added_by",
                   "severity_exceeded", "custom_metadata", "missing", "present",
                   "sort_by", "sort_direction"}
        self.assertEqual(set(MouseSearchInput.model_fields), set(FILTER_LOOKUPS) | special)


class MouseSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.protocol = Protocol.objects.create(
            protocol_number=7101, licence_reference="AI-LICENCE-A",
            name="AI imaging protocol", max_severity="Mild",
        )
        cls.other_protocol = Protocol.objects.create(
            protocol_number=7102, licence_reference="AI-LICENCE-B",
            name="AI other protocol", max_severity="Moderate",
        )
        cls.user = get_user_model().objects.create_user(
            username="ai-researcher", first_name="Ada", last_name="Scientist",
        )
        cls.crossing, cls.lines = create_test_crossing(("AI-GCaMP6s flox", "AI-Gad2"))
        cls.lines[0].aliases = "Calcium indicator,Shared alias"
        cls.lines[0].save()
        cls.lines[1].aliases = "Shared alias"
        cls.lines[1].save()
        cls.mother = Mouse.objects.create(mouse_id="AI-MOTHER", protocol=cls.other_protocol, date_added=None)
        cls.father = Mouse.objects.create(mouse_id="AI-FATHER", protocol=cls.other_protocol, date_added=None)
        cls.alpha = Mouse.objects.create(
            mouse_id="AI-001", protocol=cls.protocol, crossing_definition=cls.crossing,
            date_of_birth=date(2026, 10, 1), date_added=date(2026, 10, 2),
            protocol_start_date=date(2026, 10, 3), cull_date=date(2026, 11, 1),
            sex="F", status="Alive", id_status="assigned", tattoo="01",
            marking_type="ear_clip", colour="Black", breeding_pair="BP-ALPHA",
            severity="Moderate", added_by=cls.user, notes="Needs ear clip check. 100%_literal",
            mother=cls.mother, father=cls.father,
            mother_external_id="original-mother", father_external_id="original-father",
            genotype="GCaMP: Het; Gad2: Hom",
            custom_metadata={"batch": "B-1", "flag": False, "number": 0,
                             "nullable": None, "literal__key": "safe"},
        )
        cls.beta = Mouse.objects.create(
            mouse_id="AI-002", protocol=cls.other_protocol, crossing_definition=cls.crossing,
            date_of_birth=date(2026, 9, 1), date_added=date(2026, 9, 2),
            protocol_start_date=date(2026, 9, 3), cull_date=date(2026, 10, 1),
            sex="M", status="Culled", severity="Mild", tattoo="02", id_status="temporary",
            custom_metadata={"batch": "B-2", "flag": True, "number": 1},
        )
        cls.unknown = Mouse.objects.create(
            mouse_id="AI-NO-DOB", protocol=cls.other_protocol, date_added=None,
            genotype="Legacy Het", crossing="Legacy strain", crossing_type="Single",
        )
        for mouse, categories in ((cls.alpha, ("het", "hom")), (cls.beta, ("hom", "het"))):
            for line, category in zip(cls.lines, categories):
                MouseGenotype.objects.create(
                    mouse=mouse, mouse_line=line, zygosity=category,
                    is_uncertain=mouse == cls.beta and line == cls.lines[0],
                )

    def ids(self, **fields):
        return set(filter_mice(MouseSearchInput(**fields)).values_list("pk", flat=True))

    def test_scalar_filters_and_relationships(self):
        cases = {
            "mouse_id": "ai-001", "mouse_id_contains": "i-001",
            "id_status": "assigned", "local_identifier": "01", "marking_type": "ear_clip",
            "colour": "LAC", "sex": "F", "breeding_pair": "alpha", "notes": "EAR CLIP",
            "genotype": "gcamp: het", "protocol": "imaging", "protocol_id": self.protocol.pk,
            "protocol_number": 7101, "licence_reference": "licence-a",
            "added_by": "ADA", "added_by_id": self.user.pk,
            "mother_id": "ai-mother", "father_id": "ai-father",
            "mother_external_id": "ORIGINAL-MOTHER", "father_external_id": "ORIGINAL-FATHER",
            "severity": "Moderate",
        }
        for name, value in cases.items():
            with self.subTest(field=name):
                self.assertEqual(self.ids(**{name: value}), {self.alpha.pk})
        self.assertEqual(self.ids(status="Culled"), {self.beta.pk})

    def test_multiple_exact_ids_are_found_together(self):
        self.assertEqual(
            self.ids(mouse_ids=[self.alpha.pk, self.beta.pk]),
            {self.alpha.pk, self.beta.pk},
        )

    def test_dates_are_inclusive_for_every_recorded_date(self):
        for exact, lower, upper, value in (
            ("date_of_birth", "born_from", "born_to", "2026-10-01"),
            ("date_added", "added_from", "added_to", "2026-10-02"),
            ("protocol_start_date", "protocol_start_from", "protocol_start_to", "2026-10-03"),
            ("cull_date", "culled_from", "culled_to", "2026-11-01"),
        ):
            for fields in ({exact: value}, {lower: value}, {lower: value, upper: value}):
                with self.subTest(fields=fields):
                    self.assertEqual(self.ids(**fields), {self.alpha.pk})
            self.assertIn(self.alpha.pk, self.ids(**{upper: value}))

    def test_filters_combine_with_and(self):
        self.assertEqual(self.ids(crossing="gcamp", sex="F", status="Alive"), {self.alpha.pk})
        self.assertEqual(self.ids(crossing="gcamp", sex="F", status="Culled"), set())

    def test_crossing_names_aliases_legacy_and_duplicates(self):
        for value in ("gcamp", "CALCIUM INDICATOR", "shared alias"):
            with self.subTest(crossing=value):
                matches = filter_mice(MouseSearchInput(crossing=value))
                self.assertEqual(list(matches.order_by("pk").values_list("pk", flat=True)),
                                 [self.alpha.pk, self.beta.pk])
        self.assertEqual(self.ids(crossing="legacy STRAIN"), {self.unknown.pk})
        self.assertEqual(self.ids(crossing_id=self.crossing.pk), {self.alpha.pk, self.beta.pk})
        self.assertEqual(self.ids(species="mou"), {self.alpha.pk, self.beta.pk})

    def test_crossing_type_uses_current_lines_instead_of_stale_text(self):
        Mouse.objects.filter(pk=self.alpha.pk).update(crossing_type="WT")
        self.assertEqual(self.ids(crossing_type="Multiple"), {self.alpha.pk, self.beta.pk})
        self.assertEqual(self.ids(crossing_type="Single"), {self.unknown.pk})
        wt_crossing, _ = create_test_crossing(("AI-WT",), wild_type_names=("AI-WT",))
        wt = Mouse.objects.create(mouse_id="AI-WT", protocol=self.protocol, crossing_definition=wt_crossing)
        self.assertEqual(self.ids(crossing_type="WT"), {wt.pk})

    def test_genotype_category_and_uncertainty_match_the_same_line(self):
        GenotypeCategory.objects.filter(code="het").update(label="Heterozygous")
        for line in ("gcamp", "calcium indicator"):
            self.assertEqual(self.ids(genotypes=[{"line": line, "category": "HETEROZYGOUS"}]),
                             {self.alpha.pk})
        self.assertEqual(self.ids(genotypes=[{"line": "gcamp", "category": "het", "uncertain": True}]), set())
        self.assertEqual(self.ids(genotypes=[{"line": "gcamp", "uncertain": True}]), {self.beta.pk})
        self.assertEqual(self.ids(genotypes=[{"line": "gcamp", "uncertain": False}]), {self.alpha.pk})
        self.assertEqual(self.ids(genotypes=[{"category": "het"}]), {self.alpha.pk, self.beta.pk})

    def test_multiple_genotype_requirements_match_separate_calls(self):
        self.assertEqual(self.ids(genotypes=[{"line": "gcamp", "category": "het"},
                                            {"line": "gad2", "category": "hom"}]), {self.alpha.pk})
        self.assertEqual(self.ids(genotypes=[{"line": "gcamp", "category": "het"},
                                            {"line": "gad2", "category": "het"}]), set())

    def test_missing_present_and_unknown_choices(self):
        all_ids = {self.alpha.pk, self.beta.pk, self.unknown.pk, self.mother.pk, self.father.pk}
        self.assertEqual(self.ids(present=["date_of_birth"]), {self.alpha.pk, self.beta.pk})
        self.assertEqual(self.ids(missing=["date_of_birth"]), all_ids - {self.alpha.pk, self.beta.pk})
        self.assertEqual(self.ids(present=["notes"]), {self.alpha.pk})
        self.assertEqual(self.ids(missing=["notes"]), all_ids - {self.alpha.pk})
        self.assertEqual(self.ids(present=["mother"]), {self.alpha.pk})
        self.assertEqual(self.ids(present=["custom_metadata"]), {self.alpha.pk, self.beta.pk})
        self.assertEqual(self.ids(missing=["custom_metadata"]), {self.unknown.pk, self.mother.pk, self.father.pk})
        self.assertEqual(self.ids(sex="unknown"), {self.unknown.pk, self.mother.pk, self.father.pk})
        self.assertEqual(self.ids(severity="unknown"), {self.unknown.pk, self.mother.pk, self.father.pk})

    def test_severity_exceeded_and_false_flag(self):
        all_ids = set(Mouse.objects.values_list("pk", flat=True))
        self.assertEqual(self.ids(severity_exceeded=True), {self.alpha.pk})
        self.assertEqual(self.ids(severity_exceeded=False), all_ids - {self.alpha.pk})

    def test_custom_metadata_values_and_multiple_literal_keys(self):
        for metadata in ({"batch": "B-1"}, {"flag": False}, {"number": 0},
                         {"nullable": None}, {"literal__key": "safe"},
                         {"batch": "B-1", "flag": False}):
            with self.subTest(metadata=metadata):
                self.assertEqual(self.ids(custom_metadata=metadata), {self.alpha.pk})
        self.assertEqual(self.ids(custom_metadata={"not-present": None}), set())
        self.assertEqual(self.ids(custom_metadata={"batch": "B-1", "number": 1}), set())

    def test_sql_like_values_and_wildcards_remain_literal(self):
        self.assertEqual(self.ids(notes="100%_literal"), {self.alpha.pk})
        self.assertEqual(self.ids(notes="%' OR 1=1 --"), set())
        self.assertEqual(self.ids(mouse_id="AI-001' OR 1=1 --"), set())
        self.assertEqual(self.ids(custom_metadata={"x' OR 1=1 --": "anything"}), set())
        self.assertEqual(Mouse.objects.count(), 5)

    def test_results_handle_null_dates_and_legacy_crossings(self):
        row = search_mice(MouseSearchInput(mouse_id=self.unknown.pk))["mice"][0]
        self.assertIsNone(row["date_of_birth"])
        self.assertEqual(row["crossing"], "Legacy strain")
        self.assertEqual(row["genotype"], "Legacy Het")
        self.assertEqual(row["url"], reverse("lab:animal_summary", args=[self.unknown.pk]))
        json.dumps(row)

    def test_results_are_compact_and_use_current_genotype_labels(self):
        Mouse.objects.filter(pk=self.alpha.pk).update(notes="x" * 600)
        GenotypeCategory.objects.filter(code="het").update(label="Heterozygous")
        row = search_mice(MouseSearchInput(mouse_id=self.alpha.pk))["mice"][0]
        self.assertEqual(
            set(row),
            {"mouse_id", "date_of_birth", "crossing", "procedures", "genotype", "url", "protocol"},
        )
        self.assertIn(f"{self.lines[0].name}: Heterozygous", row["genotype"])
        self.assertIn(f"{self.lines[1].name}: Hom", row["genotype"])
        json.dumps(row)

    def test_uncertain_calls_remain_visible_in_compact_results(self):
        row = search_mice(MouseSearchInput(mouse_id=self.beta.pk))["mice"][0]
        self.assertIn(f"{self.lines[0].name}: Hom (uncertain)", row["genotype"])

    def test_empty_search_has_no_more_results(self):
        result = search_mice(MouseSearchInput(mouse_id="AI-DOES-NOT-EXIST"))
        self.assertEqual(result, {"mice": [], "has_more": False, "summary_url": None})

    def test_summary_url_contains_every_returned_animal(self):
        result = search_mice(MouseSearchInput(mouse_ids=[self.alpha.pk, self.beta.pk]))
        self.assertIn(f"selected_mouse={self.alpha.pk}", result["summary_url"])
        self.assertIn(f"selected_mouse={self.beta.pk}", result["summary_url"])

    def test_results_stay_bounded_without_per_mouse_queries(self):
        Mouse.objects.bulk_create([
            Mouse(mouse_id=f"AI-PAGE-{index:02}", protocol=self.protocol,
                  crossing_definition=self.crossing) for index in range(20)
        ])
        query = MouseSearchInput(mouse_id_contains="AI-PAGE-")
        self.assertFalse(search_mice(query)["has_more"])
        Mouse.objects.create(mouse_id="AI-PAGE-20", protocol=self.protocol, crossing_definition=self.crossing)
        with CaptureQueriesContext(connection) as queries:
            result = search_mice(query)
        self.assertEqual(len(result["mice"]), 20)
        self.assertTrue(result["has_more"])
        self.assertLessEqual(len(queries), 5)
        self.assertEqual([row["mouse_id"] for row in result["mice"]],
                         [f"AI-PAGE-{index:02}" for index in range(20)])
