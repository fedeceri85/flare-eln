from datetime import date
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from ..models import Crossing, Experiment, Mouse, MouseLine, Project, Protocol, Recording, RecordingField, RecordingType, Species


class RecordingDataMigrationTests(TransactionTestCase):
    migrate_from = [("lab", "0025_dynamic_recording_schema")]
    migrate_to = [("lab", "0029_recording_owner")]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        self.old_apps = self.executor.loader.project_state(
            self.migrate_from
        ).apps

    def tearDown(self):
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        super().tearDown()

    def test_legacy_recordings_migrate_to_generic_recordings(self):
        Protocol = self.old_apps.get_model("lab", "Protocol")
        Mouse = self.old_apps.get_model("lab", "Mouse")
        Project = self.old_apps.get_model("lab", "Project")
        Experiment = self.old_apps.get_model("lab", "Experiment")
        ImagingRecording = self.old_apps.get_model("lab", "ImagingRecording")
        ABRRecording = self.old_apps.get_model("lab", "ABRRecording")

        protocol = Protocol.objects.create(
            protocol_number=901,
            licence_reference="licence-901",
            name="Protocol 901",
        )
        mouse = Mouse.objects.create(
            mouse_id="MIG-M1",
            protocol=protocol,
        )
        project = Project.objects.create(name="Migration project")
        experiment = Experiment.objects.create(
            project=project,
            name="Migration experiment",
        )
        ImagingRecording.objects.create(
            mouse=mouse,
            experiment=experiment,
            recording_date=date(2026, 7, 20),
            data_path="/data/imaging/migration",
            sequence_number=2,
            objective="20x",
            fps=30.0,
            frames=600,
            discarded=True,
        )
        ABRRecording.objects.create(
            mouse=mouse,
            experiment=experiment,
            recording_date=date(2026, 7, 21),
            data_path="/data/abr/migration",
            sequence_number=3,
            click_threshold_db=40,
            threshold_12khz_db=25,
        )

        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        new_apps = self.executor.loader.project_state(self.migrate_to).apps
        Recording = new_apps.get_model("lab", "Recording")
        RecordingType = new_apps.get_model("lab", "RecordingType")

        self.assertEqual(Recording.objects.count(), 2)
        self.assertEqual(
            set(RecordingType.objects.values_list("slug", flat=True)),
            {"abr", "imaging"},
        )

        imaging_recording = Recording.objects.get(
            recording_type__slug="imaging"
        )
        abr_recording = Recording.objects.get(recording_type__slug="abr")

        self.assertEqual(imaging_recording.mouse_id, mouse.pk)
        self.assertEqual(imaging_recording.experiment_id, experiment.pk)
        self.assertEqual(imaging_recording.sequence_number, 2)
        self.assertEqual(imaging_recording.data_path, "/data/imaging/migration")
        self.assertEqual(imaging_recording.values["objective"], "20x")
        self.assertEqual(imaging_recording.values["fps"], 30.0)
        self.assertEqual(imaging_recording.values["frames"], 600)
        self.assertTrue(imaging_recording.values["discarded"])

        self.assertEqual(abr_recording.sequence_number, 3)
        self.assertEqual(abr_recording.data_path, "/data/abr/migration")
        self.assertEqual(abr_recording.values["click_threshold_db"], 40)
        self.assertEqual(abr_recording.values["threshold_12khz_db"], 25)


class RecordingMiceMigrationTests(TransactionTestCase):
    migrate_from = [("lab", "0035_protocol_unique_number_per_licence")]
    migrate_to = [("lab", "0036_recording_mice")]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        self.old_apps = self.executor.loader.project_state(
            self.migrate_from
        ).apps

    def tearDown(self):
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        super().tearDown()

    def test_recording_mouse_migrates_to_mice_relationship(self):
        Protocol = self.old_apps.get_model("lab", "Protocol")
        Mouse = self.old_apps.get_model("lab", "Mouse")
        Project = self.old_apps.get_model("lab", "Project")
        Experiment = self.old_apps.get_model("lab", "Experiment")
        Recording = self.old_apps.get_model("lab", "Recording")
        RecordingType = self.old_apps.get_model("lab", "RecordingType")

        protocol = Protocol.objects.create(
            protocol_number=902,
            licence_reference="licence-902",
            name="Protocol 902",
        )
        mouse = Mouse.objects.create(
            mouse_id="MIG-M2M-M1",
            protocol=protocol,
        )
        project = Project.objects.create(name="M2M migration project")
        recording_type = RecordingType.objects.create(
            name="Genotyping",
            slug="genotyping",
        )
        experiment = Experiment.objects.create(
            project=project,
            name="M2M migration experiment",
            recording_type=recording_type,
        )
        recording = Recording.objects.create(
            mouse=mouse,
            experiment=experiment,
            recording_type=recording_type,
            recording_date=date(2026, 8, 20),
            data_path="/data/genotyping/migration",
        )

        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        new_apps = self.executor.loader.project_state(self.migrate_to).apps
        Recording = new_apps.get_model("lab", "Recording")

        migrated_recording = Recording.objects.get(pk=recording.pk)
        self.assertEqual(
            list(
                migrated_recording.mice.order_by("mouse_id").values_list(
                    "mouse_id",
                    flat=True,
                )
            ),
            [mouse.pk],
        )

        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_from)
        old_apps = self.executor.loader.project_state(self.migrate_from).apps
        Recording = old_apps.get_model("lab", "Recording")
        restored_recording = Recording.objects.get(pk=recording.pk)

        self.assertEqual(restored_recording.mouse_id, mouse.pk)


class RecordingPathsMigrationTests(TransactionTestCase):
    migrate_from = [("lab", "0042_login_event")]
    migrate_to = [("lab", "0043_recording_data_paths")]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        self.old_apps = self.executor.loader.project_state(
            self.migrate_from
        ).apps

    def tearDown(self):
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        super().tearDown()

    def test_recording_paths_are_structured_and_migration_is_reversible(self):
        Project = self.old_apps.get_model("lab", "Project")
        Experiment = self.old_apps.get_model("lab", "Experiment")
        Recording = self.old_apps.get_model("lab", "Recording")
        RecordingType = self.old_apps.get_model("lab", "RecordingType")

        project = Project.objects.create(name="Paths migration project")
        recording_type = RecordingType.objects.create(
            name="Paths migration type",
            slug="paths-migration",
        )
        experiment = Experiment.objects.create(
            project=project,
            name="Paths migration experiment",
            recording_type=recording_type,
        )
        legacy_values = [
            "",
            "/data/single",
            (
                '  "C:\\recordings\\first folder"  \r\n'
                '\r\n'
                '"\\\\server\\share\\second"\r\n'
                '"\\\\server\\share\\second"'
            ),
        ]
        recording_ids = [
            Recording.objects.create(
                experiment=experiment,
                recording_type=recording_type,
                recording_date=date(2026, 9, index + 1),
                data_path=value,
            ).pk
            for index, value in enumerate(legacy_values)
        ]

        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        new_apps = self.executor.loader.project_state(self.migrate_to).apps
        Recording = new_apps.get_model("lab", "Recording")

        migrated_values = [
            Recording.objects.get(pk=recording_id).data_paths
            for recording_id in recording_ids
        ]
        self.assertEqual(
            migrated_values,
            [
                [],
                ["/data/single"],
                [
                    r"C:\recordings\first folder",
                    r"\\server\share\second",
                    r"\\server\share\second",
                ],
            ],
        )

        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_from)
        restored_apps = self.executor.loader.project_state(self.migrate_from).apps
        Recording = restored_apps.get_model("lab", "Recording")

        restored_values = [
            Recording.objects.get(pk=recording_id).data_path
            for recording_id in recording_ids
        ]
        self.assertEqual(
            restored_values,
            [
                "",
                "/data/single",
                (
                    "C:\\recordings\\first folder\n"
                    "\\\\server\\share\\second\n"
                    "\\\\server\\share\\second"
                ),
            ],
        )


class ServicesSetupMigrationTests(TransactionTestCase):
    migrate_from = [("lab", "0036_recording_mice")]
    migrate_to = [("lab", "0037_services_project_and_genotyping")]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        self.old_apps = self.executor.loader.project_state(
            self.migrate_from
        ).apps

    def tearDown(self):
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        super().tearDown()

    def test_services_project_and_genotyping_schema_are_seeded(self):
        Experiment = self.old_apps.get_model("lab", "Experiment")
        Project = self.old_apps.get_model("lab", "Project")
        RecordingField = self.old_apps.get_model("lab", "RecordingField")
        RecordingType = self.old_apps.get_model("lab", "RecordingType")

        Experiment.objects.filter(
            project__name="Services",
            name="Genotyping",
        ).delete()
        RecordingField.objects.filter(
            recording_type__slug="genotyping",
        ).delete()
        RecordingType.objects.filter(slug="genotyping").delete()
        Project.objects.filter(name="Services").delete()

        existing_services_project = Project.objects.create(
            name="Services",
            active=False,
        )

        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        new_apps = self.executor.loader.project_state(self.migrate_to).apps
        Experiment = new_apps.get_model("lab", "Experiment")
        Project = new_apps.get_model("lab", "Project")
        RecordingField = new_apps.get_model("lab", "RecordingField")
        RecordingType = new_apps.get_model("lab", "RecordingType")

        services_project = Project.objects.get(pk=existing_services_project.pk)
        self.assertTrue(services_project.is_service)
        self.assertTrue(services_project.active)
        self.assertTrue(services_project.description)

        genotyping_type = RecordingType.objects.get(slug="genotyping")
        self.assertEqual(genotyping_type.name, "Genotyping")
        self.assertEqual(
            genotyping_type.plural_name,
            "Genotyping records",
        )

        genotyping_experiment = Experiment.objects.get(
            project=services_project,
            name="Genotyping",
        )
        self.assertEqual(
            genotyping_experiment.recording_type_id,
            genotyping_type.pk,
        )
        self.assertFalse(genotyping_experiment.archived)

        fields = {
            field.key: field
            for field in RecordingField.objects.filter(
                recording_type=genotyping_type,
            )
        }
        self.assertEqual(set(fields), {"lab_member", "crossing"})
        self.assertTrue(fields["lab_member"].display_in_table)
        self.assertFalse(fields["lab_member"].filterable)
        self.assertTrue(fields["crossing"].display_in_table)
        self.assertTrue(fields["crossing"].filterable)


class StructuredCrossingsMigrationTests(TransactionTestCase):
    migrate_from = [("lab", "0037_services_project_and_genotyping")]
    migrate_to = [("lab", "0038_structured_crossings")]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        self.old_apps = self.executor.loader.project_state(
            self.migrate_from
        ).apps

    def tearDown(self):
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        super().tearDown()

    def test_structured_crossings_seed_species_without_legacy_crossings(self):
        Mouse = self.old_apps.get_model("lab", "Mouse")
        Protocol = self.old_apps.get_model("lab", "Protocol")

        protocol = Protocol.objects.create(
            protocol_number=904,
            licence_reference="licence-904",
            name="Protocol 904",
        )
        Mouse.objects.create(
            mouse_id="STRUCT-MIG-M1",
            protocol=protocol,
            crossing="Legacy line",
            crossing_type="Single",
            genotype="Legacy genotype",
        )

        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        new_apps = self.executor.loader.project_state(self.migrate_to).apps
        Crossing = new_apps.get_model("lab", "Crossing")
        Mouse = new_apps.get_model("lab", "Mouse")
        Species = new_apps.get_model("lab", "Species")

        default_species = Species.objects.get(name="Mouse")
        self.assertTrue(default_species.active)
        self.assertEqual(Crossing.objects.count(), 0)

        mouse = Mouse.objects.get(pk="STRUCT-MIG-M1")
        self.assertIsNone(mouse.crossing_definition_id)
        self.assertEqual(mouse.crossing, "Legacy line")
        self.assertEqual(mouse.crossing_type, "Single")
        self.assertEqual(mouse.genotype, "Legacy genotype")


class RenameMultipleCrossingTypeMigrationTests(TransactionTestCase):
    migrate_from = [("lab", "0038_structured_crossings")]
    migrate_to = [("lab", "0039_rename_multiple_crossing_type")]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        self.old_apps = self.executor.loader.project_state(
            self.migrate_from
        ).apps

    def tearDown(self):
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        super().tearDown()

    def test_double_triple_crossing_type_migrates_to_multiple(self):
        Mouse = self.old_apps.get_model("lab", "Mouse")
        Protocol = self.old_apps.get_model("lab", "Protocol")

        protocol = Protocol.objects.create(
            protocol_number=905,
            licence_reference="licence-905",
            name="Protocol 905",
        )
        Mouse.objects.create(
            mouse_id="MULTIPLE-MIG-M1",
            protocol=protocol,
            crossing_type="Double_triple",
        )

        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        new_apps = self.executor.loader.project_state(self.migrate_to).apps
        Mouse = new_apps.get_model("lab", "Mouse")

        mouse = Mouse.objects.get(pk="MULTIPLE-MIG-M1")
        self.assertEqual(mouse.crossing_type, "Multiple")


class SingleLineCrossingBackfillMigrationTests(TransactionTestCase):
    migrate_from = [("lab", "0039_rename_multiple_crossing_type")]
    migrate_to = [("lab", "0040_backfill_single_line_crossings")]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        self.old_apps = self.executor.loader.project_state(
            self.migrate_from
        ).apps

    def tearDown(self):
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        super().tearDown()

    def test_existing_lines_get_single_line_crossings(self):
        Crossing = self.old_apps.get_model("lab", "Crossing")
        MouseLine = self.old_apps.get_model("lab", "MouseLine")
        Species = self.old_apps.get_model("lab", "Species")

        species = Species.objects.get(name="Mouse")
        line = MouseLine.objects.create(
            species=species,
            name="Backfilled line",
        )
        self.assertFalse(
            Crossing.objects.filter(canonical_key=str(line.pk)).exists()
        )

        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        new_apps = self.executor.loader.project_state(self.migrate_to).apps
        Crossing = new_apps.get_model("lab", "Crossing")

        crossing = Crossing.objects.get(
            species_id=species.pk,
            canonical_key=str(line.pk),
        )
        self.assertEqual(crossing.display_name, "Backfilled line")
        self.assertEqual(
            list(crossing.lines.values_list("pk", flat=True)),
            [line.pk],
        )
