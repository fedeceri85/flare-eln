from datetime import date

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from pydantic import ValidationError

from flare_ai.schemas import RecordingSearchInput
from flare_ai.tools.recordings import filter_recordings, search_recordings
from lab.models import Experiment, Mouse, Project, Protocol, Recording, RecordingType


class RecordingSearchInputTests(SimpleTestCase):
    def test_requires_a_filter_and_validates_dates(self):
        with self.assertRaises(ValidationError):
            RecordingSearchInput()
        with self.assertRaises(ValidationError):
            RecordingSearchInput(
                recording_date_from="2026-11-02",
                recording_date_to="2026-11-01",
            )
        value = RecordingSearchInput(recording_date="2026-11-01")
        self.assertEqual(value.recording_date, date(2026, 11, 1))
        with self.assertRaises(ValidationError):
            RecordingSearchInput(
                animal_age_days_from=80,
                animal_age_days_to=40,
            )
        with self.assertRaises(ValidationError):
            RecordingSearchInput(animal_age_days=-1)
        with self.assertRaises(ValidationError):
            RecordingSearchInput(
                animal_age_days=30,
                animal_age_days_from=40,
            )


class RecordingSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="recording-owner")
        cls.protocol = Protocol.objects.create(
            protocol_number=8101,
            licence_reference="AI-REC",
            name="Recording protocol",
        )
        cls.mouse = Mouse.objects.create(mouse_id="AI-REC-1", protocol=cls.protocol)
        cls.kind = RecordingType.objects.create(name="Ephys", slug="ai-ephys")
        cls.project = Project.objects.create(name="AI project")
        cls.experiment = Experiment.objects.create(
            project=cls.project,
            name="Auditory responses",
            recording_type=cls.kind,
        )
        cls.recording = Recording.objects.create(
            owner=cls.user,
            experiment=cls.experiment,
            recording_type=cls.kind,
            recording_date=date(2026, 11, 1),
            data_paths=["/data/example"],
            notes="stable response",
            values={"frequency": 8000},
        )
        cls.recording.mice.add(cls.mouse)

    def test_filters_relationships_and_serializes_result(self):
        filters = RecordingSearchInput(
            experiment_contains="auditory",
            mouse_id="ai-rec-1",
            owner="recording",
        )
        self.assertEqual(list(filter_recordings(filters)), [self.recording])
        result = search_recordings(filters)
        self.assertFalse(result["has_more"])
        row = result["recordings"][0]
        self.assertEqual(row["recording_id"], self.recording.pk)
        self.assertEqual(row["mice"], [self.mouse.pk])
        self.assertEqual(row["animal_ages"], [{
            "mouse_id": self.mouse.pk,
            "age_days": None,
            "age_display": None,
            "age_filter_match": None,
        }])
        self.assertEqual(row["values"], {"frequency": 8000})
        self.assertIn("selected_recording=", row["url"])

    def test_filters_by_age_at_recording_inclusively(self):
        young = Mouse.objects.create(
            mouse_id="AI-YOUNG", protocol=self.protocol,
            date_of_birth=date(2026, 9, 6),
        )
        old = Mouse.objects.create(
            mouse_id="AI-OLD", protocol=self.protocol,
            date_of_birth=date(2026, 7, 24),
        )
        self.recording.mice.set([young, old])

        exact = RecordingSearchInput(animal_age_days=56)
        self.assertEqual(list(filter_recordings(exact)), [self.recording])
        bounded = RecordingSearchInput(
            animal_age_days_from=56,
            animal_age_days_to=56,
        )
        self.assertEqual(list(filter_recordings(bounded)), [self.recording])
        self.assertFalse(filter_recordings(
            RecordingSearchInput(animal_age_days_from=57, animal_age_days_to=99)
        ).exists())

        row = search_recordings(exact)["recordings"][0]
        by_id = {entry["mouse_id"]: entry for entry in row["animal_ages"]}
        self.assertEqual(by_id[young.pk], {
            "mouse_id": young.pk,
            "age_days": 56,
            "age_display": "P56",
            "age_filter_match": True,
        })
        self.assertEqual(by_id[old.pk]["age_days"], 100)
        self.assertFalse(by_id[old.pk]["age_filter_match"])

    def test_animal_id_and_age_must_match_the_same_linked_animal(self):
        target_old = Mouse.objects.create(
            mouse_id="AI-TARGET", protocol=self.protocol,
            date_of_birth=date(2026, 7, 24),
        )
        other_young = Mouse.objects.create(
            mouse_id="AI-OTHER", protocol=self.protocol,
            date_of_birth=date(2026, 9, 6),
        )
        self.recording.mice.set([target_old, other_young])
        filters = RecordingSearchInput(
            mouse_id="AI-TARGET",
            animal_age_days=56,
        )
        self.assertFalse(filter_recordings(filters).exists())

        target_old.date_of_birth = date(2026, 9, 6)
        target_old.save(update_fields=["date_of_birth"])
        self.assertEqual(list(filter_recordings(filters)), [self.recording])

    def test_age_filter_excludes_missing_and_future_birth_dates(self):
        missing = Mouse.objects.create(mouse_id="AI-MISSING", protocol=self.protocol)
        future = Mouse.objects.create(
            mouse_id="AI-FUTURE", protocol=self.protocol,
            date_of_birth=date(2026, 11, 2),
        )
        self.recording.mice.set([missing, future])
        self.assertFalse(filter_recordings(
            RecordingSearchInput(animal_age_days_from=0)
        ).exists())
