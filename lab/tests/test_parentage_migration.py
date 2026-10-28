from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ParentageMigrationTests(TransactionTestCase):
    def test_existing_animals_and_history_are_preserved_with_empty_parentage(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        previous = [('lab', '0061_mouse_date_added')]
        target = [('lab', '0062_mouse_father_mouse_father_external_id_mouse_mother_and_more')]
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps
        protocol = apps.get_model('lab', 'Protocol').objects.create(protocol_number=878, licence_reference='MIG', name='Parent migration')
        historical = apps.get_model('lab', 'Mouse')
        historical.objects.create(mouse_id='Preserved / mouse', protocol=protocol, notes='Original history', breeding_pair='Do not infer')
        before = list(historical.objects.values())
        executor = MigrationExecutor(connection); executor.migrate(target)
        historical = executor.loader.project_state(target).apps.get_model('lab', 'Mouse')
        after = list(historical.objects.values())
        for row in after:
            self.assertIsNone(row.pop('mother_id')); self.assertIsNone(row.pop('father_id'))
            self.assertEqual(row.pop('mother_external_id'), ''); self.assertEqual(row.pop('father_external_id'), '')
        self.assertEqual(before, after)
