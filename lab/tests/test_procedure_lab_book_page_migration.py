from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ProcedureLabBookPageMigrationTests(TransactionTestCase):
    def test_lab_book_page_moves_to_notes_and_field_is_removed(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        previous = [("lab", "0075_remove_legacy_recording_crossing_values")]
        target = [("lab", "0076_move_procedure_lab_book_page_to_notes")]
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps
        protocol = apps.get_model("lab", "Protocol").objects.create(
            protocol_number=976, licence_reference="MIG", name="Historical"
        )
        procedure_type = apps.get_model("lab", "ProcedureType").objects.create(name="Historical procedure")
        mouse = apps.get_model("lab", "Mouse").objects.create(mouse_id="MIG-PROC-1", protocol=protocol)
        Procedure = apps.get_model("lab", "Procedure")
        empty_notes = Procedure.objects.create(mouse=mouse, procedure_type=procedure_type, lab_book_page="42")
        existing_notes = Procedure.objects.create(
            mouse=mouse, procedure_type=procedure_type, lab_book_page=" 7 ", notes="Existing note"
        )
        untouched = Procedure.objects.create(mouse=mouse, procedure_type=procedure_type, notes="No page recorded")

        executor = MigrationExecutor(connection)
        executor.migrate(target)
        apps = executor.loader.project_state(target).apps
        Procedure = apps.get_model("lab", "Procedure")

        notes = {procedure.pk: procedure.notes for procedure in Procedure.objects.all()}
        self.assertEqual(notes[empty_notes.pk], "Lab book page: 42")
        self.assertEqual(notes[existing_notes.pk], "Existing note\nLab book page: 7")
        self.assertEqual(notes[untouched.pk], "No page recorded")
        self.assertNotIn("lab_book_page", [field.name for field in Procedure._meta.fields])
