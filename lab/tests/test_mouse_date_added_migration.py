from datetime import date

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class MouseDateAddedMigrationTests(TransactionTestCase):
    def test_backfill_prefers_protocol_start_then_dob_and_preserves_missing_dates(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        previous = [("lab", "0060_remove_recordingimage_caption_and_more")]
        target = [("lab", "0061_mouse_date_added")]
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps
        protocol = apps.get_model("lab", "Protocol").objects.create(
            protocol_number=927, licence_reference="MIG", name="Historical dates",
        )
        Mouse = apps.get_model("lab", "Mouse")
        for mouse_id, started, born in [
            ("BOTH", date(2021, 2, 3), date(2020, 1, 1)),
            ("START-ONLY", date(2022, 3, 4), None),
            ("DOB-ONLY", None, date(2020, 4, 5)),
            ("NEITHER", None, None),
        ]:
            Mouse.objects.create(
                mouse_id=mouse_id, protocol=protocol,
                protocol_start_date=started, date_of_birth=born,
            )
        before = list(Mouse.objects.order_by("pk").values())
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        Mouse = executor.loader.project_state(target).apps.get_model("lab", "Mouse")
        self.assertEqual(dict(Mouse.objects.values_list("pk", "date_added")), {
            "BOTH": date(2021, 2, 3),
            "START-ONLY": date(2022, 3, 4),
            "DOB-ONLY": date(2020, 4, 5),
            "NEITHER": None,
        })
        after = list(Mouse.objects.order_by("pk").values())
        for row in after:
            row.pop("date_added")
        self.assertEqual(before, after)
