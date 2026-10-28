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
