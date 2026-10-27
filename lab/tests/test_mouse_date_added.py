from datetime import date

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from ..models import Mouse, Protocol
from ._support import AuthenticatedTestCase


@override_settings(ALLOWED_HOSTS=["testserver"])
class MouseDateAddedTests(AuthenticatedTestCase):
    def setUp(self):
        self.protocol = Protocol.objects.create(
            protocol_number=926, licence_reference="DATE", name="Date test",
        )
        self.url = reverse("lab:procedure_page_licence_protocol", args=["DATE", 926])

    def test_new_mice_get_today_and_edits_preserve_date_added(self):
        mouse = Mouse.objects.create(
            mouse_id="NEW", protocol=self.protocol,
            protocol_start_date=date(2020, 1, 1),
        )
        mouse.refresh_from_db()
        self.assertEqual(mouse.date_added, timezone.localdate())
        Mouse.objects.filter(pk=mouse.pk).update(date_added=date(2021, 2, 3))
        mouse.refresh_from_db()
        mouse.notes = "Edited"
        mouse.save()
        mouse.refresh_from_db()
        self.assertEqual(mouse.date_added, date(2021, 2, 3))

    def test_default_sort_and_explicit_directions_put_unknown_dates_last(self):
        for mouse_id, added, started in [
            ("OLDER", date(2021, 1, 1), date(2023, 1, 1)),
            ("RECENT-B", date(2022, 1, 1), date(2020, 1, 1)),
            ("RECENT-A", date(2022, 1, 1), date(2020, 1, 1)),
            ("UNKNOWN", None, None),
        ]:
            Mouse.objects.create(
                mouse_id=mouse_id, protocol=self.protocol,
                date_added=added, protocol_start_date=started,
            )

        for params, expected in [
            ({}, ["RECENT-A", "RECENT-B", "OLDER", "UNKNOWN"]),
            ({"sort": "invalid"}, ["RECENT-A", "RECENT-B", "OLDER", "UNKNOWN"]),
            ({"sort": "date_added", "direction": "desc"}, ["RECENT-A", "RECENT-B", "OLDER", "UNKNOWN"]),
            ({"sort": "date_added", "direction": "asc"}, ["OLDER", "RECENT-A", "RECENT-B", "UNKNOWN"]),
        ]:
            with self.subTest(params=params):
                response = self.client.get(self.url, {"show_current_year": "0", **params})
                table = response.context["table"]
                self.assertEqual(
                    [row["cells"][0]["checkbox"]["value"] for row in table["rows"]],
                    expected,
                )
                column = next(col for col in table["columns"] if col.get("label") == "Date added")
                self.assertEqual(column["sort_state"], params.get("direction", "desc"))
                self.assertContains(response, "Date added")
