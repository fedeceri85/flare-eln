from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class MouseAddedByMigrationTests(TransactionTestCase):
    def test_existing_records_preserved_and_creator_left_unknown(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        previous = [('lab', '0051_remove_mouse_unique_mouse_birth_pair_crossing_marking_and_more')]
        target = [('lab', '0052_mouse_added_by')]
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps
        apps.get_model('auth', 'User').objects.create(username='legacy-admin', is_staff=True, is_superuser=True)
        protocol = apps.get_model('lab', 'Protocol').objects.create(protocol_number=923, licence_reference='MIG', name='Historical')
        apps.get_model('lab', 'Mouse').objects.create(mouse_id='PRESERVE', protocol=protocol, notes='Original notes')
        before = list(apps.get_model('lab', 'Mouse').objects.order_by('pk').values())
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        apps = executor.loader.project_state(target).apps
        after = list(apps.get_model('lab', 'Mouse').objects.order_by('pk').values())
        self.assertTrue(all(row.pop('added_by_id') is None for row in after))
        self.assertEqual(before, after)
