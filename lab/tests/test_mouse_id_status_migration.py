from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class MouseIDStatusMigrationTests(TransactionTestCase):
    def test_existing_names_are_unknown_and_lookup_index_is_created(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        previous = [("lab", "0049_alter_mouse_marking_type")]
        try:
            executor.migrate(previous)
            old_apps = executor.loader.project_state(previous).apps
            Protocol = old_apps.get_model("lab", "Protocol")
            Mouse = old_apps.get_model("lab", "Mouse")
            protocol = Protocol.objects.create(protocol_number=1, licence_reference="ID-MIGRATION", name="Test")
            mouse_ids = ("12345", "20260610_pair_gene_1")
            for mouse_id in mouse_ids:
                Mouse.objects.create(mouse_id=mouse_id, protocol=protocol)
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            new_apps = executor.loader.project_state(latest).apps
            Mouse = new_apps.get_model("lab", "Mouse")
            self.assertEqual(set(Mouse.objects.filter(pk__in=mouse_ids).values_list("id_status", flat=True)), {"unknown"})
            with connection.cursor() as cursor:
                indexes = connection.introspection.get_constraints(cursor, Mouse._meta.db_table)
            self.assertEqual(indexes["mouse_birth_crossing_idx"]["columns"], ["date_of_birth", "crossing_definition_id"])
        finally:
            MigrationExecutor(connection).migrate(latest)
