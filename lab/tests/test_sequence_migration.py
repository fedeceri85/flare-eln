from datetime import date

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class SequenceFieldMigrationTests(TransactionTestCase):
    previous = [("lab", "0054_spreadsheetimportdraft")]
    target = [("lab", "0055_sequence_recording_field")]

    def setUp(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        executor.migrate(self.previous)
        self.apps = executor.loader.project_state(self.previous).apps
        # A rollback retains the configurable field/data; simulate an actual
        # pre-migration installation without that field.
        self.apps.get_model("lab", "RecordingField").objects.filter(
            recording_type__slug="imaging", key="sequence_number",
        ).delete()

    def migrate(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.target)
        return executor.loader.project_state(self.target).apps

    def recording(self, slug, sequence, values=None):
        kind, _ = self.apps.get_model("lab", "RecordingType").objects.get_or_create(
            slug=slug, defaults={"name": "Renamed " + slug},
        )
        project = self.apps.get_model("lab", "Project").objects.create(name="Sequence migration")
        experiment = self.apps.get_model("lab", "Experiment").objects.create(
            project=project, name="Historical recordings", recording_type=kind,
        )
        return self.apps.get_model("lab", "Recording").objects.create(
            recording_type=kind, experiment=experiment, sequence_number=sequence,
            recording_date=date(2026, 10, 21), values=values or {},
            notes="Keep these notes", data_paths=["/data/unchanged"],
        )

    def test_exact_imaging_values_preserved_and_other_types_have_no_sequence(self):
        imaging = [self.recording("imaging", number, {"objective": "20x"}) for number in (0, 1, 7, 2147483647)]
        other = self.recording("other", 8, {"custom": "unchanged"})
        owner = self.apps.get_model("auth", "User").objects.create(username="historical-owner")
        protocol = self.apps.get_model("lab", "Protocol").objects.create(protocol_number=955, licence_reference="MIG", name="Migration")
        mouse = self.apps.get_model("lab", "Mouse").objects.create(mouse_id="SEQUENCE-MOUSE", protocol=protocol)
        imaging[0].owner = owner
        imaging[0].save()
        imaging[0].mice.set([mouse])
        kind = self.apps.get_model("lab", "RecordingType").objects.get(pk=imaging[0].recording_type_id)
        kind.name = "My renamed microscope"
        kind.save()
        before = {row.pk: {key: value for key, value in row.__dict__.items() if key not in {"_state", "sequence_number", "values"}} for row in [*imaging, other]}
        apps = self.migrate()
        Recording = apps.get_model("lab", "Recording")
        field = apps.get_model("lab", "RecordingField").objects.get(recording_type_id=kind.pk, key="sequence_number")
        self.assertEqual(field.data_type, "integer")
        self.assertEqual(field.label, "Sequence")
        self.assertFalse(field.required)
        self.assertEqual(field.default_value, 1)
        self.assertTrue(field.display_in_table)
        for original in imaging:
            migrated = Recording.objects.get(pk=original.pk)
            self.assertEqual(migrated.values, {"objective": "20x", "sequence_number": original.sequence_number})
        migrated = Recording.objects.get(pk=imaging[0].pk)
        self.assertEqual(migrated.owner_id, owner.pk)
        self.assertEqual(list(migrated.mice.values_list("pk", flat=True)), [mouse.pk])
        self.assertEqual(Recording.objects.get(pk=other.pk).values, {"custom": "unchanged"})
        for row in Recording.objects.filter(pk__in=before):
            self.assertEqual({key: value for key, value in row.__dict__.items() if key not in {"_state", "values"}}, before[row.pk])
        self.assertFalse(apps.get_model("lab", "RecordingField").objects.filter(recording_type_id=other.recording_type_id, key="sequence_number").exists())
        self.assertNotIn("sequence_number", [field.name for field in Recording._meta.fields])
        with connection.cursor() as cursor:
            columns = connection.introspection.get_table_description(cursor, Recording._meta.db_table)
        self.assertNotIn("sequence_number", [column.name for column in columns])

    def test_installation_without_imaging_does_not_get_an_imaging_type_or_field(self):
        self.apps.get_model("lab", "RecordingType").objects.filter(slug="imaging").delete()
        other = self.recording("other", 4)
        apps = self.migrate()
        self.assertFalse(apps.get_model("lab", "RecordingType").objects.filter(slug="imaging").exists())
        self.assertFalse(apps.get_model("lab", "RecordingField").objects.filter(key="sequence_number").exists())
        self.assertEqual(apps.get_model("lab", "Recording").objects.get(pk=other.pk).values, {})

    def test_existing_compatible_field_keeps_its_configuration(self):
        row = self.recording("imaging", 9, {"sequence_number": 9})
        field = self.apps.get_model("lab", "RecordingField").objects.create(
            recording_type_id=row.recording_type_id, key="sequence_number", label="Acquisition", data_type="integer",
            required=True, default_value=3, filterable=True, order=75,
        )
        apps = self.migrate()
        migrated = apps.get_model("lab", "RecordingField").objects.get(pk=field.pk)
        self.assertEqual((migrated.label, migrated.required, migrated.default_value, migrated.filterable, migrated.order), ("Acquisition", True, 3, True, 75))

    def test_conflicting_values_abort_before_legacy_column_is_removed(self):
        first = self.recording("imaging", 3)
        conflict = self.recording("imaging", 4, {"sequence_number": 99})
        try:
            with self.assertRaisesMessage(RuntimeError, "conflicting legacy and configurable sequence values"):
                self.migrate()
            first.refresh_from_db()
            self.assertEqual(first.sequence_number, 3)
            self.assertEqual(first.values, {})  # Earlier copies were rolled back too.
            self.assertFalse(self.apps.get_model("lab", "RecordingField").objects.filter(recording_type_id=first.recording_type_id, key="sequence_number").exists())
        finally:
            self.apps.get_model("lab", "Recording").objects.filter(pk=conflict.pk).update(values={})

    def test_incompatible_field_aborts_without_changing_its_configuration(self):
        row = self.recording("imaging", 3)
        Field = self.apps.get_model("lab", "RecordingField")
        field = Field.objects.create(recording_type_id=row.recording_type_id, key="sequence_number", label="Custom sequence", data_type="text")
        try:
            with self.assertRaisesMessage(RuntimeError, "must be an integer"):
                self.migrate()
            row.refresh_from_db()
            self.assertEqual(row.sequence_number, 3)
            field.refresh_from_db()
            self.assertEqual(field.data_type, "text")
        finally:
            field.delete()

    def test_active_import_drafts_remap_sequence_and_require_fresh_review(self):
        imaging = self.recording("imaging", 3)
        other = self.recording("other", 4)
        owner = self.apps.get_model("auth", "User").objects.create(username="sequence-import")
        Draft = self.apps.get_model("lab", "SpreadsheetImportDraft")
        binding = {"heading": "Sequence", "occurrence": 1}
        drafts = [Draft.objects.create(
            owner=owner, experiment_id=row.experiment_id, filename="data.csv",
            configuration={"version": 1, "recording_type": slug, "bindings": {"sequence_number": binding}, "defaults": {"sequence_number": 2}},
            state={"review_fingerprint": "stale", "selected_rows": [2], "animals": {"resolved": {"mouse_id": "EXISTING"}}},
        ) for row, slug in [(imaging, "imaging"), (other, "other")]]
        completed = Draft.objects.create(
            owner=owner, experiment_id=imaging.experiment_id, filename="done.csv", completed_at=timezone.now(),
            configuration={"defaults": {"sequence_number": 1}}, state={"result": {"recordings": 1}},
        )
        apps = self.migrate()
        for original in drafts:
            draft = apps.get_model("lab", "SpreadsheetImportDraft").objects.get(pk=original.pk)
            self.assertNotIn("sequence_number", draft.configuration["bindings"])
            self.assertNotIn("sequence_number", draft.configuration["defaults"])
            if draft.experiment_id == imaging.experiment_id:
                self.assertEqual(draft.configuration["bindings"]["field:sequence_number"], binding)
                self.assertEqual(draft.configuration["defaults"]["field:sequence_number"], 2)
            else:
                self.assertEqual(draft.configuration["bindings"], {})
                self.assertEqual(draft.configuration["defaults"], {})
            self.assertEqual(draft.state, {key: value for key, value in original.state.items() if key != "review_fingerprint"})
        completed.refresh_from_db()
        self.assertEqual(completed.configuration, {"defaults": {"sequence_number": 1}})

    def test_rollback_restores_imaging_sequences_from_configurable_values(self):
        row = self.recording("imaging", 27, {"objective": "20x"})
        self.migrate()
        executor = MigrationExecutor(connection)
        executor.migrate(self.previous)
        apps = executor.loader.project_state(self.previous).apps
        restored = apps.get_model("lab", "Recording").objects.get(pk=row.pk)
        self.assertEqual(restored.sequence_number, 27)
        self.assertEqual(restored.values, {"objective": "20x", "sequence_number": 27})
