from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class RecordingBuiltinMigrationTests(TransactionTestCase):
    def test_existing_types_keep_both_columns_visible_and_recording_values(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        previous = [('lab', '0062_mouse_father_mouse_father_external_id_mouse_mother_and_more')]
        target = [('lab', '0063_recording_type_builtin_column_visibility')]
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps
        kind = apps.get_model('lab', 'RecordingType').objects.create(name='Preserved built-in', slug='preserved-built-in')
        project = apps.get_model('lab', 'Project').objects.create(name='Preserved built-in project')
        experiment = apps.get_model('lab', 'Experiment').objects.create(project=project, name='Preserved built-in experiment', recording_type=kind)
        recording = apps.get_model('lab', 'Recording').objects.create(experiment=experiment, recording_type=kind,
            recording_date='2026-10-01', data_paths=['/keep/data'], notes='Keep notes')
        executor = MigrationExecutor(connection); executor.migrate(target)
        apps = executor.loader.project_state(target).apps
        kind = apps.get_model('lab', 'RecordingType').objects.get(pk=kind.pk)
        recording = apps.get_model('lab', 'Recording').objects.get(pk=recording.pk)
        self.assertFalse(kind.hide_data_paths_in_table); self.assertFalse(kind.hide_notes_in_table)
        self.assertEqual(recording.data_paths, ['/keep/data']); self.assertEqual(recording.notes, 'Keep notes')

    def test_existing_recording_types_enable_animal_age(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        previous = [("lab", "0070_daily_ai_usage_events")]
        target = [("lab", "0071_recordingtype_show_animal_age")]
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps
        kind = apps.get_model("lab", "RecordingType").objects.create(
            name="Age migration", slug="age-migration"
        )
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        apps = executor.loader.project_state(target).apps
        kind = apps.get_model("lab", "RecordingType").objects.get(pk=kind.pk)
        self.assertTrue(kind.show_animal_age)

    def test_genotyping_crossing_values_survive_field_removal_and_rollback(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        previous = [("lab", "0073_recordingtype_crossing_genotype_visibility")]
        target = [("lab", "0074_remove_genotyping_crossing_field")]
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps
        RecordingField = apps.get_model("lab", "RecordingField")
        RecordingType = apps.get_model("lab", "RecordingType")
        genotyping = RecordingType.objects.create(
            name="Genotyping",
            plural_name="Genotyping records",
            slug="genotyping",
        )
        RecordingField.objects.create(
            recording_type=genotyping,
            key="crossing",
            label="Crossing",
            data_type="text",
            display_in_table=True,
            filterable=True,
            order=20,
        )
        project = apps.get_model("lab", "Project").objects.create(
            name="Historical genotyping project"
        )
        experiment = apps.get_model("lab", "Experiment").objects.create(
            project=project,
            name="Historical genotyping experiment",
            recording_type=genotyping,
        )
        recording = apps.get_model("lab", "Recording").objects.create(
            experiment=experiment,
            recording_type=genotyping,
            recording_date="2026-10-01",
            values={"crossing": "Historical crossing", "lab_member": "Ada"},
        )
        self.assertTrue(
            RecordingField.objects.filter(
                recording_type=genotyping, key="crossing"
            ).exists()
        )

        executor = MigrationExecutor(connection)
        executor.migrate(target)
        apps = executor.loader.project_state(target).apps
        migrated_recording = apps.get_model("lab", "Recording").objects.get(
            pk=recording.pk
        )
        self.assertEqual(
            migrated_recording.values,
            {"crossing": "Historical crossing", "lab_member": "Ada"},
        )
        self.assertFalse(
            apps.get_model("lab", "RecordingField").objects.filter(
                recording_type_id=genotyping.pk, key="crossing"
            ).exists()
        )

        executor = MigrationExecutor(connection)
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps
        restored_field = apps.get_model("lab", "RecordingField").objects.get(
            recording_type_id=genotyping.pk, key="crossing"
        )
        restored_recording = apps.get_model("lab", "Recording").objects.get(
            pk=recording.pk
        )
        self.assertTrue(restored_field.active)
        self.assertTrue(restored_field.display_in_table)
        self.assertTrue(restored_field.filterable)
        self.assertEqual(restored_recording.values["crossing"], "Historical crossing")
        # The following migration intentionally rejects orphaned genotyping
        # records, so remove this migration-0074-only fixture before cleanup.
        restored_recording.delete()

    def test_legacy_crossing_cleanup_requires_valid_linked_animals(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        previous = [("lab", "0074_remove_genotyping_crossing_field")]
        target = [("lab", "0075_remove_legacy_recording_crossing_values")]
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps
        RecordingType = apps.get_model("lab", "RecordingType")
        genotyping = RecordingType.objects.create(
            name="Genotyping cleanup",
            plural_name="Genotyping cleanup records",
            slug="genotyping",
        )
        project = apps.get_model("lab", "Project").objects.create(
            name="Genotyping cleanup project"
        )
        experiment = apps.get_model("lab", "Experiment").objects.create(
            project=project,
            name="Genotyping cleanup experiment",
            recording_type=genotyping,
        )
        protocol = apps.get_model("lab", "Protocol").objects.create(
            protocol_number=975,
            licence_reference="MIGRATION-75",
            name="Migration 75 protocol",
        )
        valid_mouse = apps.get_model("lab", "Mouse").objects.create(
            mouse_id="MIGRATION-75-VALID",
            protocol=protocol,
            crossing="Animal-derived crossing",
        )
        missing_crossing_mouse = apps.get_model("lab", "Mouse").objects.create(
            mouse_id="MIGRATION-75-MISSING",
            protocol=protocol,
        )
        Recording = apps.get_model("lab", "Recording")
        valid_recording = Recording.objects.create(
            experiment=experiment,
            recording_type=genotyping,
            recording_date="2026-10-02",
            values={"crossing": "Obsolete value", "lab_member": "Ada"},
        )
        valid_recording.mice.add(valid_mouse)
        no_animal_recording = Recording.objects.create(
            experiment=experiment,
            recording_type=genotyping,
            recording_date="2026-10-03",
            values={"crossing": "No animal value"},
        )
        missing_crossing_recording = Recording.objects.create(
            experiment=experiment,
            recording_type=genotyping,
            recording_date="2026-10-04",
            values={"crossing": "Missing crossing value"},
        )
        missing_crossing_recording.mice.add(missing_crossing_mouse)

        with self.assertRaisesRegex(
            RuntimeError,
            "recordings without animals.*animals missing crossing data",
        ):
            MigrationExecutor(connection).migrate(target)

        valid_recording.refresh_from_db()
        self.assertEqual(valid_recording.values["crossing"], "Obsolete value")

        no_animal_recording.mice.add(valid_mouse)
        missing_crossing_mouse.crossing = "Repaired animal crossing"
        missing_crossing_mouse.save(update_fields=["crossing"])
        MigrationExecutor(connection).migrate(target)
        apps = MigrationExecutor(connection).loader.project_state(target).apps
        Recording = apps.get_model("lab", "Recording")

        self.assertEqual(
            Recording.objects.get(pk=valid_recording.pk).values,
            {"lab_member": "Ada"},
        )
        self.assertEqual(
            Recording.objects.get(pk=no_animal_recording.pk).values,
            {},
        )
        self.assertEqual(
            Recording.objects.get(pk=missing_crossing_recording.pk).values,
            {},
        )
