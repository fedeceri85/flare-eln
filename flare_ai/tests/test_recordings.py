from datetime import date

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from pydantic import ValidationError

from flare_ai.schemas import RecordingSearchInput
from flare_ai.tools.recordings import filter_recordings, search_recordings
from lab.models import (
    Experiment,
    Mouse,
    MouseGenotype,
    Project,
    Protocol,
    Recording,
    RecordingType,
)
from lab.tests._support import create_test_crossing


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

    def test_mouse_id_and_mouse_ids_are_mutually_exclusive(self):
        with self.assertRaises(ValidationError):
            RecordingSearchInput(
                mouse_id="M1",
                mouse_ids=["M1", "M2"],
            )

    def test_mouse_ids_with_property_filters_is_valid(self):
        value = RecordingSearchInput(
            mouse_ids=["M1", "M2"],
            crossing="Gad2",
            sex="F",
        )
        self.assertEqual(value.mouse_ids, ["M1", "M2"])
        self.assertEqual(value.crossing, "Gad2")
        self.assertEqual(value.sex, "F")

    def test_new_property_fields_accept_valid_values(self):
        value = RecordingSearchInput(
            crossing="TMC1",
            crossing_type="Single",
            sex="F",
            colour="black",
            genotype="Gad2: het",
            species="rat",
            breeding_pair="P1",
            status="Alive",
            severity="Mild",
        )
        self.assertEqual(value.crossing, "TMC1")
        self.assertEqual(value.crossing_type, "Single")
        self.assertEqual(value.sex, "F")
        self.assertEqual(value.colour, "black")
        self.assertEqual(value.genotype, "Gad2: het")
        self.assertEqual(value.species, "rat")
        self.assertEqual(value.breeding_pair, "P1")
        self.assertEqual(value.status, "Alive")
        self.assertEqual(value.severity, "Mild")


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

    def _make_recording_with_mouse(self, mouse, **overrides):
        recording = Recording.objects.create(
            owner=self.user,
            experiment=self.experiment,
            recording_type=self.kind,
            recording_date=date(2026, 11, 1),
            data_paths=[],
            values={},
            **overrides,
        )
        recording.mice.add(mouse)
        return recording

    def test_filters_by_crossing_display_name(self):
        crossing, _ = create_test_crossing(line_names=("Gad2",))
        mouse = Mouse.objects.create(
            mouse_id="AI-GAD2", protocol=self.protocol,
            crossing_definition=crossing,
        )
        rec = self._make_recording_with_mouse(mouse)
        results = list(filter_recordings(RecordingSearchInput(crossing="gad2")))
        self.assertIn(rec, results)

    def test_filters_by_legacy_crossing_text(self):
        mouse = Mouse.objects.create(
            mouse_id="AI-LEGACY", protocol=self.protocol,
            crossing="Gad2 x Cdh23",
        )
        rec = self._make_recording_with_mouse(mouse)
        results = list(filter_recordings(RecordingSearchInput(crossing="gad2")))
        self.assertIn(rec, results)

    def test_filters_by_crossing_line_name(self):
        crossing, _ = create_test_crossing(line_names=("Tmc1",))
        mouse = Mouse.objects.create(
            mouse_id="AI-TMC1", protocol=self.protocol,
            crossing_definition=crossing,
        )
        rec = self._make_recording_with_mouse(mouse)
        results = list(filter_recordings(RecordingSearchInput(crossing="tmc1")))
        self.assertIn(rec, results)

    def test_filters_by_crossing_line_alias(self):
        crossing, lines = create_test_crossing(line_names=("Gad2",))
        lines[0].aliases = "GABA-A2;GABRA2"
        lines[0].save()
        mouse = Mouse.objects.create(
            mouse_id="AI-ALIAS", protocol=self.protocol,
            crossing_definition=crossing,
        )
        rec = self._make_recording_with_mouse(mouse)
        results = list(filter_recordings(RecordingSearchInput(crossing="GABRA2")))
        self.assertIn(rec, results)

    def test_filters_by_crossing_type_single(self):
        crossing, _ = create_test_crossing(line_names=("Gad2",))
        mouse = Mouse.objects.create(
            mouse_id="AI-SINGLE", protocol=self.protocol,
            crossing_definition=crossing,
        )
        rec = self._make_recording_with_mouse(mouse)
        results = list(filter_recordings(RecordingSearchInput(crossing_type="Single")))
        self.assertIn(rec, results)

    def test_filters_by_crossing_type_multiple(self):
        crossing, _ = create_test_crossing(line_names=("Gad2", "Cdh23"))
        mouse = Mouse.objects.create(
            mouse_id="AI-MULT", protocol=self.protocol,
            crossing_definition=crossing,
        )
        rec = self._make_recording_with_mouse(mouse)
        results = list(filter_recordings(RecordingSearchInput(crossing_type="Multiple")))
        self.assertIn(rec, results)

    def test_filters_by_crossing_type_wt(self):
        crossing, _ = create_test_crossing(
            line_names=("C57",), wild_type_names=("C57",),
        )
        mouse = Mouse.objects.create(
            mouse_id="AI-WT", protocol=self.protocol,
            crossing_definition=crossing,
        )
        rec = self._make_recording_with_mouse(mouse)
        results = list(filter_recordings(RecordingSearchInput(crossing_type="WT")))
        self.assertIn(rec, results)

    def test_filters_by_sex(self):
        female = Mouse.objects.create(mouse_id="AI-F", protocol=self.protocol, sex="F")
        male = Mouse.objects.create(mouse_id="AI-M", protocol=self.protocol, sex="M")
        rec_f = self._make_recording_with_mouse(female)
        rec_m = self._make_recording_with_mouse(male)
        results = list(filter_recordings(RecordingSearchInput(sex="F")))
        self.assertIn(rec_f, results)
        self.assertNotIn(rec_m, results)

    def test_filters_by_sex_unknown_matches_empty(self):
        unknown = Mouse.objects.create(mouse_id="AI-UNK", protocol=self.protocol, sex="")
        rec = self._make_recording_with_mouse(unknown)
        results = list(filter_recordings(RecordingSearchInput(sex="unknown")))
        self.assertIn(rec, results)

    def test_filters_by_colour(self):
        mouse = Mouse.objects.create(mouse_id="AI-BLK", protocol=self.protocol, colour="black")
        rec = self._make_recording_with_mouse(mouse)
        results = list(filter_recordings(RecordingSearchInput(colour="black")))
        self.assertIn(rec, results)

    def test_filters_by_genotype(self):
        mouse = Mouse.objects.create(
            mouse_id="AI-GENO", protocol=self.protocol, genotype="Gad2: het; Cdh23: het",
        )
        rec = self._make_recording_with_mouse(mouse)
        results = list(filter_recordings(RecordingSearchInput(genotype="Gad2")))
        self.assertIn(rec, results)

    def test_filters_by_structured_genotype_calls(self):
        _, lines = create_test_crossing(line_names=("Gad2",))
        mouse = Mouse.objects.create(
            mouse_id="AI-CALL", protocol=self.protocol,
        )
        MouseGenotype.objects.create(mouse=mouse, mouse_line=lines[0], zygosity="het")
        rec = self._make_recording_with_mouse(mouse)
        results = list(filter_recordings(RecordingSearchInput(genotype="Gad2")))
        self.assertIn(rec, results)

    def test_filters_by_species(self):
        crossing, _ = create_test_crossing(line_names=("Gad2",), species_name="Rat")
        mouse = Mouse.objects.create(
            mouse_id="AI-RAT", protocol=self.protocol, crossing_definition=crossing,
        )
        rec = self._make_recording_with_mouse(mouse)
        results = list(filter_recordings(RecordingSearchInput(species="rat")))
        self.assertIn(rec, results)

    def test_filters_by_breeding_pair(self):
        mouse = Mouse.objects.create(mouse_id="AI-BP", protocol=self.protocol, breeding_pair="P42")
        rec = self._make_recording_with_mouse(mouse)
        results = list(filter_recordings(RecordingSearchInput(breeding_pair="P42")))
        self.assertIn(rec, results)

    def test_filters_by_status(self):
        culled = Mouse.objects.create(mouse_id="AI-CULL", protocol=self.protocol, status="Culled")
        rec = self._make_recording_with_mouse(culled)
        results = list(filter_recordings(RecordingSearchInput(status="Culled")))
        self.assertIn(rec, results)

    def test_filters_by_severity(self):
        mouse = Mouse.objects.create(mouse_id="AI-SEV", protocol=self.protocol, severity="Mild")
        rec = self._make_recording_with_mouse(mouse)
        results = list(filter_recordings(RecordingSearchInput(severity="Mild")))
        self.assertIn(rec, results)

    def test_filters_by_severity_unknown_matches_empty(self):
        mouse = Mouse.objects.create(mouse_id="AI-NOSEV", protocol=self.protocol, severity="")
        rec = self._make_recording_with_mouse(mouse)
        results = list(filter_recordings(RecordingSearchInput(severity="unknown")))
        self.assertIn(rec, results)

    def test_mouse_ids_requires_all_listed_present(self):
        mouse_a = Mouse.objects.create(mouse_id="AI-A", protocol=self.protocol)
        mouse_b = Mouse.objects.create(mouse_id="AI-B", protocol=self.protocol)
        mouse_c = Mouse.objects.create(mouse_id="AI-C", protocol=self.protocol)
        rec_ab = Recording.objects.create(
            owner=self.user, experiment=self.experiment,
            recording_type=self.kind, recording_date=date(2026, 11, 1),
            data_paths=[], values={},
        )
        rec_ab.mice.add(mouse_a, mouse_b)
        rec_ac = Recording.objects.create(
            owner=self.user, experiment=self.experiment,
            recording_type=self.kind, recording_date=date(2026, 11, 1),
            data_paths=[], values={},
        )
        rec_ac.mice.add(mouse_a, mouse_c)
        results = list(filter_recordings(RecordingSearchInput(mouse_ids=["AI-A", "AI-B"])))
        self.assertIn(rec_ab, results)
        self.assertNotIn(rec_ac, results)
        results = list(filter_recordings(RecordingSearchInput(mouse_ids=["AI-A", "AI-C"])))
        self.assertIn(rec_ac, results)
        self.assertNotIn(rec_ab, results)

    def test_mouse_ids_combines_with_property_filters(self):
        crossing, _ = create_test_crossing(line_names=("Gad2",))
        mouse_gad2 = Mouse.objects.create(
            mouse_id="AI-MG", protocol=self.protocol, crossing_definition=crossing,
        )
        mouse_other = Mouse.objects.create(mouse_id="AI-MO", protocol=self.protocol)
        rec = Recording.objects.create(
            owner=self.user, experiment=self.experiment,
            recording_type=self.kind, recording_date=date(2026, 11, 1),
            data_paths=[], values={},
        )
        rec.mice.add(mouse_gad2, mouse_other)
        results = list(filter_recordings(RecordingSearchInput(
            mouse_ids=["AI-MG", "AI-MO"], crossing="Gad2",
        )))
        self.assertIn(rec, results)

    def test_crossing_and_sex_must_match_same_mouse(self):
        crossing, _ = create_test_crossing(line_names=("Gad2",))
        mouse_a = Mouse.objects.create(
            mouse_id="AI-CA", protocol=self.protocol,
            crossing_definition=crossing, sex="M",
        )
        mouse_b = Mouse.objects.create(mouse_id="AI-CB", protocol=self.protocol, sex="F")
        rec = Recording.objects.create(
            owner=self.user, experiment=self.experiment,
            recording_type=self.kind, recording_date=date(2026, 11, 1),
            data_paths=[], values={},
        )
        rec.mice.add(mouse_a, mouse_b)
        # No single mouse is both Gad2 AND female
        results = list(filter_recordings(RecordingSearchInput(crossing="Gad2", sex="F")))
        self.assertNotIn(rec, results)
        # But sex=F alone should match (mouse B is female)
        results = list(filter_recordings(RecordingSearchInput(sex="F")))
        self.assertIn(rec, results)
        # And crossing=Gad2 alone should match (mouse A is Gad2)
        results = list(filter_recordings(RecordingSearchInput(crossing="Gad2")))
        self.assertIn(rec, results)

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

    def test_pins_to_project_and_experiment(self):
        other_project = Project.objects.create(name="Other AI project")
        other_experiment = Experiment.objects.create(
            project=other_project, name="Other responses", recording_type=self.kind,
        )
        other_recording = Recording.objects.create(
            owner=self.user,
            experiment=other_experiment,
            recording_type=self.kind,
            recording_date=date(2026, 11, 5),
            data_paths=[],
            values={},
        )
        other_recording.mice.add(self.mouse)

        by_project = filter_recordings(
            RecordingSearchInput(project_id=self.project.project_id)
        )
        self.assertEqual(list(by_project), [self.recording])
        by_experiment = filter_recordings(
            RecordingSearchInput(experiment_id=self.experiment.experiment_id)
        )
        self.assertEqual(list(by_experiment), [self.recording])
        both = filter_recordings(
            RecordingSearchInput(
                project_id=self.project.project_id,
                experiment_id=self.experiment.experiment_id,
            )
        )
        self.assertEqual(list(both), [self.recording])
        self.assertNotIn(
            self.recording,
            list(
                filter_recordings(
                    RecordingSearchInput(
                        project_id=other_project.project_id,
                        experiment_id=self.experiment.experiment_id,
                    )
                )
            ),
        )
