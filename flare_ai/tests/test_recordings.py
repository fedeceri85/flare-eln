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
        self.assertEqual(row["values"], {"frequency": 8000})
        self.assertIn("selected_recording=", row["url"])
