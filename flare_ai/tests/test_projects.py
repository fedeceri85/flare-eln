from datetime import date

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from pydantic import ValidationError

from flare_ai.schemas import ProjectSearchInput
from flare_ai.tools.projects import (
    PROJECT_FILTER_LOOKUPS,
    filter_projects,
    search_projects,
)
from lab.models import Experiment, Project


class ProjectSearchInputTests(SimpleTestCase):
    def test_unknown_fields_are_rejected(self):
        with self.assertRaises(ValidationError):
            ProjectSearchInput(name="Calcium", invented_field="x")

    def test_every_input_has_an_implemented_filter(self):
        self.assertEqual(
            set(ProjectSearchInput.model_fields),
            set(PROJECT_FILTER_LOOKUPS),
        )

    def test_false_boolean_and_date_bounds_are_valid(self):
        query = ProjectSearchInput(
            active=False,
            start_date_from="2026-01-01",
            start_date_to="2026-12-31",
        )
        self.assertEqual(query.active, False)
        self.assertEqual(query.start_date_from, date(2026, 1, 1))


class ProjectSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.calcium = Project.objects.create(
            name="Calcium imaging",
            lab_member="Federico Rossi",
            description="Two-photon calcium imaging",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 6, 30),
        )
        cls.legacy = Project.objects.create(
            name="Legacy behaviour",
            lab_member="Someone Else",
            description="Archived behaviour work",
            active=False,
            start_date=date(2026, 6, 1),
        )
        cls.services = Project.objects.create(
            name="AI service project",
            lab_member="Federico Rossi",
            is_service=True,
        )
        cls.experiment = Experiment.objects.create(
            project=cls.calcium, name="Calcium sessions",
        )

    def ids(self, **fields):
        return set(filter_projects(ProjectSearchInput(**fields)).values_list("pk", flat=True))

    def test_scalar_filters(self):
        self.assertIn(self.calcium.pk, self.ids(name="calcium imaging"))
        self.assertNotIn(self.legacy.pk, self.ids(name="calcium imaging"))
        self.assertIn(self.legacy.pk, self.ids(name="legacy behaviour"))
        self.assertNotIn(self.calcium.pk, self.ids(name="legacy behaviour"))
        self.assertIn(self.calcium.pk, self.ids(lab_member="federico"))
        self.assertNotIn(self.legacy.pk, self.ids(lab_member="federico"))
        self.assertIn(self.calcium.pk, self.ids(description="two-photon"))
        self.assertNotIn(self.legacy.pk, self.ids(description="two-photon"))
        self.assertIn(self.legacy.pk, self.ids(active=False))
        self.assertNotIn(self.calcium.pk, self.ids(active=False))

    def test_filters_combine_with_and(self):
        self.assertNotIn(self.calcium.pk, self.ids(name="calcium imaging", active=False))
        self.assertNotIn(self.legacy.pk, self.ids(name="calcium imaging", active=False))
        combined = self.ids(name="legacy behaviour", active=False, lab_member="someone")
        self.assertIn(self.legacy.pk, combined)
        self.assertNotIn(self.calcium.pk, combined)

    def test_service_projects_are_hidden_by_default_and_found_on_request(self):
        self.assertIn(self.services.pk, self.ids(is_service=True))
        self.assertNotIn(self.calcium.pk, self.ids(is_service=True))
        self.assertNotIn(self.services.pk, self.ids(is_service=False))
        self.assertIn(self.calcium.pk, self.ids(is_service=False))
        # The default (no is_service) hides service projects.
        default = self.ids(lab_member="federico")
        self.assertIn(self.calcium.pk, default)
        self.assertNotIn(self.services.pk, default)

    def test_date_bounds_are_inclusive(self):
        exact = self.ids(start_date_from="2026-01-01", start_date_to="2026-01-01")
        self.assertIn(self.calcium.pk, exact)
        self.assertNotIn(self.legacy.pk, exact)
        from_bound = self.ids(start_date_from="2026-06-01")
        self.assertIn(self.legacy.pk, from_bound)
        self.assertNotIn(self.calcium.pk, from_bound)
        self.assertIn(self.calcium.pk, self.ids(end_date_from="2026-06-30"))

    def test_search_returns_fields_count_and_url(self):
        result = search_projects(ProjectSearchInput(name="calcium imaging"))
        row = next(item for item in result["projects"] if item["project_id"] == self.calcium.pk)
        self.assertEqual(row["name"], "Calcium imaging")
        self.assertEqual(row["lab_member"], "Federico Rossi")
        self.assertEqual(row["active"], True)
        self.assertEqual(row["start_date"], "2026-01-01")
        self.assertEqual(row["end_date"], "2026-06-30")
        self.assertEqual(row["experiment_count"], 1)
        self.assertEqual(
            row["url"],
            reverse("lab:experiments_page", args=[self.calcium.pk]),
        )
