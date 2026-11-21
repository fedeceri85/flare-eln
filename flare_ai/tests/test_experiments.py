from datetime import date

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from pydantic import ValidationError

from flare_ai.schemas import ExperimentSearchInput
from flare_ai.tools.experiments import (
    EXPERIMENT_FILTER_LOOKUPS,
    filter_experiments,
    search_experiments,
)
from lab.models import Experiment, Project, Recording, RecordingType


class ExperimentSearchInputTests(SimpleTestCase):
    def test_unknown_fields_are_rejected(self):
        with self.assertRaises(ValidationError):
            ExperimentSearchInput(name="Tone", invented_field="x")

    def test_project_id_is_positive(self):
        with self.assertRaises(ValidationError):
            ExperimentSearchInput(project_id=0)

    def test_every_input_has_an_implemented_filter(self):
        self.assertEqual(
            set(ExperimentSearchInput.model_fields),
            set(EXPERIMENT_FILTER_LOOKUPS),
        )


class ExperimentSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.kind = RecordingType.objects.create(name="Ephys", slug="ai-ephys")
        cls.calcium = Project.objects.create(name="Calcium imaging")
        cls.services = Project.objects.create(name="AI service project", is_service=True)
        cls.tone = Experiment.objects.create(
            project=cls.calcium,
            name="Tone responses",
            description="Auditory evoked responses",
            recording_type=cls.kind,
            start_date=date(2026, 2, 1),
        )
        cls.legacy = Experiment.objects.create(
            project=cls.calcium,
            name="Legacy imaging",
            archived=True,
        )
        cls.service_experiment = Experiment.objects.create(
            project=cls.services, name="Service ephys", recording_type=cls.kind,
        )

    def ids(self, **fields):
        return set(
            filter_experiments(ExperimentSearchInput(**fields)).values_list("pk", flat=True)
        )

    def test_scalar_and_fk_filters(self):
        self.assertIn(self.tone.pk, self.ids(name="tone responses"))
        self.assertNotIn(self.legacy.pk, self.ids(name="tone responses"))
        self.assertNotIn(self.service_experiment.pk, self.ids(name="tone responses"))
        self.assertIn(self.tone.pk, self.ids(description="auditory"))
        self.assertNotIn(self.legacy.pk, self.ids(description="auditory"))
        self.assertIn(self.tone.pk, self.ids(archived=False))
        self.assertNotIn(self.legacy.pk, self.ids(archived=False))
        self.assertIn(self.legacy.pk, self.ids(archived=True))
        self.assertNotIn(self.tone.pk, self.ids(archived=True))

    def test_fk_lookups_traverse_to_a_leaf(self):
        # Filtering must traverse the FK to a concrete column, never apply
        # icontains to the FK itself (which would raise a FieldError).
        by_project = self.ids(project="calcium imaging")
        self.assertIn(self.tone.pk, by_project)
        self.assertIn(self.legacy.pk, by_project)
        self.assertNotIn(self.service_experiment.pk, by_project)
        by_type = self.ids(recording_type="ephys")
        self.assertIn(self.tone.pk, by_type)
        self.assertNotIn(self.legacy.pk, by_type)
        pinned = self.ids(project_id=self.calcium.project_id, recording_type="ephys")
        self.assertIn(self.tone.pk, pinned)
        self.assertNotIn(self.legacy.pk, pinned)

    def test_service_experiments_are_hidden_by_default_and_found_on_request(self):
        self.assertIn(self.service_experiment.pk, self.ids(is_service=True))
        self.assertNotIn(self.tone.pk, self.ids(is_service=True))
        self.assertNotIn(self.legacy.pk, self.ids(is_service=True))
        self.assertNotIn(self.service_experiment.pk, self.ids(is_service=False))
        self.assertIn(self.tone.pk, self.ids(is_service=False))
        self.assertIn(self.legacy.pk, self.ids(is_service=False))
        # The default (no is_service) hides service experiments.
        default = self.ids(archived=False)
        self.assertIn(self.tone.pk, default)
        self.assertNotIn(self.service_experiment.pk, default)

    def test_date_bounds_are_inclusive(self):
        exact = self.ids(start_date_from="2026-02-01", start_date_to="2026-02-01")
        self.assertIn(self.tone.pk, exact)
        self.assertNotIn(self.legacy.pk, exact)

    def test_filters_combine_with_and(self):
        self.assertNotIn(self.tone.pk, self.ids(name="tone responses", archived=True))
        combined = self.ids(project="calcium imaging", name="legacy imaging", archived=True)
        self.assertIn(self.legacy.pk, combined)
        self.assertNotIn(self.tone.pk, combined)

    def test_search_returns_fields_count_and_url(self):
        result = search_experiments(ExperimentSearchInput(name="tone responses"))
        row = next(item for item in result["experiments"] if item["experiment_id"] == self.tone.pk)
        self.assertEqual(row["name"], "Tone responses")
        self.assertEqual(row["project"], "Calcium imaging")
        self.assertEqual(row["project_id"], self.calcium.project_id)
        self.assertEqual(row["recording_type"], "Ephys")
        self.assertEqual(row["recording_count"], 0)
        self.assertEqual(row["archived"], False)
        self.assertEqual(
            row["url"],
            reverse("lab:recordings_page", args=[self.calcium.project_id, self.tone.pk]),
        )

    def test_recording_count_is_reported(self):
        Recording.objects.create(
            experiment=self.tone,
            recording_type=self.kind,
            recording_date=date(2026, 2, 3),
            data_paths=[],
            values={},
        )
        result = search_experiments(ExperimentSearchInput(name="tone responses"))
        row = next(item for item in result["experiments"] if item["experiment_id"] == self.tone.pk)
        self.assertEqual(row["recording_count"], 1)
