from datetime import date
import json
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import override_settings
from django.urls import reverse
from ..forms.recordings import RecordingForm
from ..models import Experiment, Mouse, Project, Protocol, Recording, RecordingField, RecordingType
from ._support import AuthenticatedTestCase, create_recording


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class RecordingModelTests(AuthenticatedTestCase):
    def setUp(self):
        self.protocol = Protocol.objects.create(
            protocol_number=401,
            licence_reference="licence-401",
            name="Protocol 401",
        )
        self.mouse = Mouse.objects.create(
            mouse_id="REC-M1",
            protocol=self.protocol,
        )
        self.project = Project.objects.create(
            name="Auditory project",
        )
        self.imaging_type = RecordingType.objects.get(slug="imaging")
        self.abr_type = RecordingType.objects.get(slug="abr")
        self.experiment = Experiment.objects.create(
            project=self.project,
            name="Experiment A",
            recording_type=self.imaging_type,
        )
        self.abr_experiment = Experiment.objects.create(
            project=self.project,
            name="Experiment B",
            recording_type=self.abr_type,
        )

    def test_generic_recordings_store_base_and_custom_fields(self):
        imaging_recording = create_recording(
            mouse=self.mouse,
            experiment=self.experiment,
            recording_type=self.imaging_type,
            recording_date=date(2026, 7, 20),
            data_paths=["/data/imaging/session-1"],
            values={
                "sequence_number": 2,
                "objective": "20x",
                "fps": 30.0,
                "frames": 600,
                "discarded": False,
            },
        )
        abr_recording = create_recording(
            mouse=self.mouse,
            experiment=self.abr_experiment,
            recording_type=self.abr_type,
            recording_date=date(2026, 7, 21),
            data_paths=["/data/abr/session-1"],
            values={
                "click_threshold_db": 35,
                "threshold_12khz_db": 20,
            },
        )

        self.assertEqual(imaging_recording.pk, imaging_recording.recording_id)
        self.assertEqual(abr_recording.pk, abr_recording.recording_id)
        self.assertEqual(
            imaging_recording.data_paths,
            ["/data/imaging/session-1"],
        )
        self.assertEqual(abr_recording.data_paths, ["/data/abr/session-1"])
        self.assertEqual(imaging_recording.values["objective"], "20x")
        self.assertEqual(abr_recording.values["click_threshold_db"], 35)

        response = self.client.get(
            reverse("lab:animal_summary", args=[self.mouse.pk]),
        )
        project_url = reverse("lab:experiments_page", args=[self.project.pk])
        experiment_url = reverse(
            "lab:recordings_page",
            args=[self.project.pk, self.experiment.pk],
        )
        selected_recording_url = (
            f"{experiment_url}?selected_recording={imaging_recording.pk}"
            f"#recording-{imaging_recording.pk}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Imaging recordings")
        self.assertContains(response, "ABR recordings")
        self.assertContains(response, "/data/imaging/session-1")
        self.assertContains(response, "/data/abr/session-1")
        self.assertContains(response, "20x")
        self.assertContains(response, "30.0")
        self.assertContains(response, "600")
        self.assertContains(response, "35")
        self.assertContains(response, "20")
        self.assert_contextual_link(response, project_url, response.wsgi_request.get_full_path())
        self.assert_contextual_link(response, experiment_url, response.wsgi_request.get_full_path())
        self.assert_contextual_link(
            response, selected_recording_url, response.wsgi_request.get_full_path(),
        )

    def test_recording_string_handles_missing_mouse(self):
        recording = create_recording(
            experiment=self.experiment,
            recording_type=self.imaging_type,
            recording_date=date(2026, 7, 20),
            data_paths=["/data/imaging/orphaned-session"],
            values={"sequence_number": 1},
        )

        self.assertIn("No mice", str(recording))

    def test_sequence_uses_configurable_fields_on_any_type_and_can_be_left_blank(self):
        kind = RecordingType.objects.create(name="Custom acquisition", slug="custom-acquisition")
        experiment = Experiment.objects.create(project=self.project, name="Custom experiment", recording_type=kind)
        field = RecordingField.objects.create(
            recording_type=kind, key="sequence_number", label="Acquisition order", data_type="integer",
        )
        name = f"value_{field.pk}"
        for value, expected in [("17", {"sequence_number": 17}), ("", {})]:
            with self.subTest(value=value):
                form = RecordingForm(data={"recording_date": "2026-10-21", name: value}, experiment=experiment, owner=self.user)
                self.assertNotIn("sequence_number", form.fields)
                self.assertEqual(form.fields[name].label, "Acquisition order")
                self.assertFalse(form.fields[name].required)
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.save().values, expected)
        invalid = RecordingForm(data={"recording_date": "2026-10-21", name: "1.5"}, experiment=experiment)
        self.assertFalse(invalid.is_valid())
        self.assertIn(name, invalid.errors)
        field.required = True
        field.save()
        required = RecordingForm(data={"recording_date": "2026-10-21"}, experiment=experiment)
        self.assertFalse(required.is_valid())
        self.assertIn(name, required.errors)

    def test_sequence_is_absent_without_a_configured_field(self):
        form = RecordingForm(data={"recording_date": "2026-10-21"}, experiment=self.abr_experiment, owner=self.user)
        self.assertNotIn("sequence_number", form.fields)
        self.assertFalse(any(field.key == "sequence_number" for _, field in form.dynamic_fields))
        self.assertTrue(form.is_valid(), form.errors)
        recording = form.save()
        self.assertNotIn("sequence_number", recording.values)
        self.assertNotIn("#", str(recording))

        # Even a type named imaging has no runtime exception: its fields alone
        # determine whether Sequence is available.
        RecordingField.objects.filter(recording_type=self.imaging_type, key="sequence_number").delete()
        imaging = RecordingForm(experiment=self.experiment)
        self.assertFalse(any(field.key == "sequence_number" for _, field in imaging.dynamic_fields))

    def test_standalone_import_notebook_saves_configurable_sequence_and_animal_links(self):
        notebook = json.loads((Path(__file__).resolve().parents[2] / "import_imaging_recordings.ipynb").read_text())
        source = next("".join(cell["source"]) for cell in notebook["cells"] if "def apply_prepared_record(" in "".join(cell.get("source", [])))
        record = {"mouse": self.mouse, "recording_date": date(2026, 10, 21), "data_paths": ["/notebook/run"], "notes": "Imported", "values": {"sequence_number": 15}}
        namespace = {
            "validation_errors": [], "prepared_records": [record], "Recording": Recording,
            "experiment": self.experiment, "recording_type": self.imaging_type, "owner": self.user,
            "DRY_RUN": False, "ALLOW_UPDATES": False, "transaction": transaction, "print": lambda *args: None,
        }
        exec(compile(source, "import_imaging_recordings.ipynb", "exec"), namespace)
        saved = self.experiment.recordings.get()
        self.assertEqual(saved.values, {"sequence_number": 15})
        self.assertEqual(list(saved.mice.all()), [self.mouse])
        record["values"] = {"sequence_number": 37}
        namespace["ALLOW_UPDATES"] = True
        exec(compile(source, "import_imaging_recordings.ipynb", "exec"), namespace)
        saved.refresh_from_db()
        self.assertEqual(saved.values, {"sequence_number": 37})
        self.assertEqual(list(saved.mice.all()), [self.mouse])
        self.assertEqual(self.experiment.recordings.count(), 1)

    def test_recording_paths_allow_an_empty_list(self):
        recording = Recording(
            experiment=self.experiment,
            recording_type=self.imaging_type,
            recording_date=date(2026, 7, 20),
            data_paths=[],
        )

        recording.full_clean()

    def test_recording_paths_reject_invalid_values(self):
        invalid_values = [
            "/data/not-a-list",
            [""],
            ["   "],
            [123],
        ]

        for data_paths in invalid_values:
            with self.subTest(data_paths=data_paths):
                recording = Recording(
                    experiment=self.experiment,
                    recording_type=self.imaging_type,
                    recording_date=date(2026, 7, 20),
                    data_paths=data_paths,
                )

                with self.assertRaises(ValidationError) as context:
                    recording.full_clean()

                self.assertIn("data_paths", context.exception.message_dict)

    def test_recording_can_be_modified_by_owner_or_staff(self):
        User = get_user_model()
        owner = User.objects.create_user(
            username="recording-owner",
            password="password",
        )
        other_user = User.objects.create_user(
            username="recording-other",
            password="password",
        )
        staff_user = User.objects.create_user(
            username="recording-admin",
            password="password",
            is_staff=True,
        )
        recording = create_recording(
            owner=owner,
            mouse=self.mouse,
            experiment=self.experiment,
            recording_type=self.imaging_type,
            recording_date=date(2026, 7, 20),
            data_paths=["/data/imaging/owned-session"],
            values={"sequence_number": 1},
        )

        self.assertTrue(recording.can_be_modified_by(owner))
        self.assertTrue(recording.can_be_modified_by(staff_user))
        self.assertFalse(recording.can_be_modified_by(other_user))

    def test_recording_values_are_validated_against_type_fields(self):
        valid_recording = Recording(
            experiment=self.experiment,
            recording_type=self.imaging_type,
            recording_date=date(2026, 7, 20),
            data_paths=["/data/imaging/valid"],
            values={
                "objective": "20x",
                "fps": 30.0,
                "frames": 600,
                "discarded": False,
            },
        )

        valid_recording.full_clean()

        invalid_recording = Recording(
            experiment=self.experiment,
            recording_type=self.imaging_type,
            recording_date=date(2026, 7, 20),
            data_paths=["/data/imaging/invalid"],
            values={
                "fps": "fast",
            },
        )

        with self.assertRaises(ValidationError):
            invalid_recording.full_clean()

    def test_recording_values_reject_unknown_fields(self):
        recording = Recording(
            experiment=self.experiment,
            recording_type=self.imaging_type,
            recording_date=date(2026, 7, 20),
            data_paths=["/data/imaging/unknown"],
            values={
                "unknown_field": "value",
            },
        )

        with self.assertRaises(ValidationError):
            recording.full_clean()

    def test_recording_type_must_match_experiment_type(self):
        recording = Recording(
            experiment=self.experiment,
            recording_type=self.abr_type,
            recording_date=date(2026, 7, 20),
            data_paths=["/data/abr/wrong-experiment"],
        )

        with self.assertRaises(ValidationError):
            recording.full_clean()

    def test_recording_field_key_cannot_change_when_values_exist(self):
        create_recording(
            mouse=self.mouse,
            experiment=self.experiment,
            recording_type=self.imaging_type,
            recording_date=date(2026, 7, 20),
            data_paths=["/data/imaging/key-lock"],
            values={
                "fps": 30.0,
            },
        )
        field = RecordingField.objects.get(
            recording_type=self.imaging_type,
            key="fps",
        )
        field.key = "hz"

        with self.assertRaises(ValidationError):
            field.full_clean()
