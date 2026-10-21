from datetime import date
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from ..test_navigation import Markup
from ..models import Mouse, MAX_SEVERITY_CHOICES, Procedure, ProcedureType, Protocol
from ._support import AuthenticatedTestCase


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class ReportsPageTests(AuthenticatedTestCase):
    def setUp(self):
        Protocol.objects.all().delete()

        self.current_year = timezone.localdate().year
        self.today = timezone.localdate()
        self.licence = "licence-report"
        self.other_licence = "licence-other"
        self.protocol_1 = Protocol.objects.create(
            protocol_number=701,
            licence_reference=self.licence,
            name="Procedure 701",
            max_severity="Mild",
        )
        self.protocol_2 = Protocol.objects.create(
            protocol_number=702,
            licence_reference=self.licence,
            name="Procedure 702",
            max_severity="Moderate",
        )
        self.other_protocol = Protocol.objects.create(
            protocol_number=801,
            licence_reference=self.other_licence,
            name="Procedure 801",
            max_severity="Mild",
        )
        self.non_regulated_protocol = Protocol.objects.create(
            protocol_number=802,
            licence_reference=self.licence,
            name="Non-regulated procedure",
            max_severity="Mild",
            allows_regulated_procedures=False,
        )
        self.surgery = ProcedureType.objects.create(
            name="Surgery",
        )
        self.abr = ProcedureType.objects.create(
            name="ABR",
        )
        self.report_url = reverse("lab:reports_page")

        self.mild_mouse = Mouse.objects.create(
            mouse_id="REPORT-MILD",
            protocol=self.protocol_1,
            protocol_start_date=date(self.current_year, 1, 5),
            severity="Mild",
            crossing_type="WT",
        )
        self.moderate_mouse = Mouse.objects.create(
            mouse_id="REPORT-MODERATE-EXCEEDED",
            protocol=self.protocol_1,
            protocol_start_date=date(self.current_year, 1, 6),
            severity="Moderate",
            crossing_type="Single",
        )
        self.severe_mouse = Mouse.objects.create(
            mouse_id="REPORT-SEVERE-EXCEEDED",
            protocol=self.protocol_2,
            protocol_start_date=date(self.current_year, 1, 7),
            severity="Severe",
            crossing_type="Multiple",
        )
        self.not_set_mouse = Mouse.objects.create(
            mouse_id="REPORT-NOT-SET",
            protocol=self.protocol_2,
            protocol_start_date=date(self.current_year, 1, 8),
        )
        self.old_mouse = Mouse.objects.create(
            mouse_id="REPORT-OLD",
            protocol=self.protocol_1,
            protocol_start_date=date(self.current_year - 1, 12, 31),
            severity="Severe",
        )
        self.other_licence_mouse = Mouse.objects.create(
            mouse_id="REPORT-OTHER-LICENCE",
            protocol=self.other_protocol,
            protocol_start_date=date(self.current_year, 1, 9),
            severity="Severe",
        )
        self.no_start_mouse = Mouse.objects.create(
            mouse_id="REPORT-NO-START",
            protocol=self.protocol_1,
            severity="Mild",
        )
        self.non_regulated_mouse = Mouse.objects.create(
            mouse_id="REPORT-NON-REGULATED",
            protocol=self.non_regulated_protocol,
            protocol_start_date=date(self.current_year, 1, 10),
            severity="Mild",
        )
        Procedure.objects.create(
            mouse=self.mild_mouse,
            procedure_type=self.surgery,
        )
        Procedure.objects.create(
            mouse=self.moderate_mouse,
            procedure_type=self.surgery,
        )
        Procedure.objects.create(
            mouse=self.moderate_mouse,
            procedure_type=self.surgery,
        )
        Procedure.objects.create(
            mouse=self.severe_mouse,
            procedure_type=self.abr,
        )
        Procedure.objects.create(
            mouse=self.not_set_mouse,
            procedure_type=self.surgery,
        )
        Procedure.objects.create(
            mouse=self.old_mouse,
            procedure_type=self.abr,
        )
        Procedure.objects.create(
            mouse=self.other_licence_mouse,
            procedure_type=self.surgery,
        )
        Procedure.objects.create(
            mouse=self.non_regulated_mouse,
            procedure_type=self.abr,
        )

    def home_office_query(self, output_mode="table"):
        return (
            f"{self.report_url}?report=home_office_return"
            f"&licence_reference={self.licence}"
            f"&start_date=01/01/{self.current_year}"
            f"&end_date={self.today:%d/%m/%Y}"
            f"&output_mode={output_mode}"
        )

    def test_home_page_renders_active_reports_card(self):
        response = self.client.get(reverse("lab:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reports")
        self.assertContains(response, f'href="{self.report_url}"')
        markup = Markup(response.content.decode())
        mice_links = markup.find("a", href=reverse("lab:mice_page"))
        self.assertTrue(mice_links)
        self.assertTrue(all(link.within("nav") for link in mice_links))

    def test_reports_page_renders_mice_card(self):
        response = self.client.get(self.report_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mice")
        self.assertContains(response, f'href="{reverse("lab:mice_page")}"')

    def test_home_office_return_form_defaults_to_current_year_to_today(self):
        response = self.client.get(
            f"{self.report_url}?report=home_office_return"
        )
        form = response.context["home_office_form"]

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Home Office Return")
        self.assertContains(
            response,
            f'<a class="card" href="{self.report_url}?report=home_office_return">',
        )
        self.assertContains(response, "Generate report")
        self.assertEqual(
            form.fields["start_date"].initial,
            date(self.current_year, 1, 1),
        )
        self.assertEqual(form.fields["end_date"].initial, self.today)
        self.assertContains(
            response,
            f'name="start_date" value="01/01/{self.current_year}"',
        )
        self.assertContains(
            response,
            f'name="end_date" value="{self.today:%d/%m/%Y}"',
        )
        self.assertContains(response, 'placeholder="dd/mm/yyyy"')
        self.assertEqual(
            list(form.fields["licence_reference"].choices),
            [
                (self.other_licence, self.other_licence),
                (self.licence, self.licence),
            ],
        )
        self.assertIsNone(response.context["home_office_report"])

    def test_home_office_return_counts_by_licence_and_protocol_start_date(self):
        response = self.client.get(self.home_office_query())
        report = response.context["home_office_report"]
        protocol_rows = {
            row["protocol"].pk: row
            for row in report["protocol_rows"]
        }
        procedure_rows = {
            row["procedure_type"].pk: row
            for row in report["procedure_rows"]
        }
        total_crossing_type_rows = {
            row["label"]: row["count"]
            for row in report["total_crossing_type_rows"]
        }
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            report["total"],
            {
                "total": 4,
                "non-recovery": 0,
                "severity_rows": [
                    {"label": label, "count": 0 if value == "Non-recovery" else 1}
                    for value, label in MAX_SEVERITY_CHOICES
                ],
                "mild": 1,
                "moderate": 1,
                "severe": 1,
                "not_set": 1,
                "exceeded": 2,
            },
        )
        self.assertEqual(
            total_crossing_type_rows,
            {
                "Wild Type": 1,
                "Single Mutation": 1,
                "Multiple": 1,
                "Unknown": 1,
            },
        )
        self.assertEqual(protocol_rows[self.protocol_1.pk]["total"], 2)
        self.assertEqual(protocol_rows[self.protocol_1.pk]["mild"], 1)
        self.assertEqual(protocol_rows[self.protocol_1.pk]["moderate"], 1)
        self.assertEqual(protocol_rows[self.protocol_1.pk]["exceeded"], 1)
        self.assertEqual(
            {
                row["label"]: row["count"]
                for row in protocol_rows[self.protocol_1.pk]["crossing_type_rows"]
            },
            {
                "Wild Type": 1,
                "Single Mutation": 1,
                "Multiple": 0,
                "Unknown": 0,
            },
        )
        self.assertEqual(protocol_rows[self.protocol_2.pk]["total"], 2)
        self.assertEqual(protocol_rows[self.protocol_2.pk]["severe"], 1)
        self.assertEqual(protocol_rows[self.protocol_2.pk]["not_set"], 1)
        self.assertEqual(protocol_rows[self.protocol_2.pk]["exceeded"], 1)
        self.assertEqual(
            {
                row["label"]: row["count"]
                for row in protocol_rows[self.protocol_2.pk]["crossing_type_rows"]
            },
            {
                "Wild Type": 0,
                "Single Mutation": 0,
                "Multiple": 1,
                "Unknown": 1,
            },
        )
        self.assertEqual(procedure_rows[self.surgery.pk]["total"], 3)
        self.assertEqual(procedure_rows[self.surgery.pk]["mild"], 1)
        self.assertEqual(procedure_rows[self.surgery.pk]["moderate"], 1)
        self.assertEqual(procedure_rows[self.surgery.pk]["not_set"], 1)
        self.assertEqual(procedure_rows[self.surgery.pk]["exceeded"], 1)
        self.assertEqual(procedure_rows[self.abr.pk]["total"], 1)
        self.assertEqual(procedure_rows[self.abr.pk]["severe"], 1)
        self.assertEqual(procedure_rows[self.abr.pk]["exceeded"], 1)
        self.assertContains(
            response,
            "Mice are assigned to this return based on protocol start date",
        )
        self.assertContains(response, "Total by crossing type")
        self.assertContains(response, "Unknown")
        self.assertContains(response, "Breakdown by protocol")
        self.assertContains(response, "Breakdown by procedure")
        self.assertLess(
            content.index("Breakdown by protocol"),
            content.index("Breakdown by procedure"),
        )

    def test_home_office_return_text_mode_renders_same_numbers(self):
        response = self.client.get(self.home_office_query(output_mode="text"))
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            (
                f"Date range: 01/01/{self.current_year} "
                f"to {self.today:%d/%m/%Y}"
            ),
        )
        self.assertContains(response, "Mice under procedure: 4")
        self.assertContains(response, "Mild: 1")
        self.assertContains(response, "Moderate: 1")
        self.assertContains(response, "Severe: 1")
        self.assertContains(response, "Severity not set: 1")
        self.assertContains(response, "Surpassed severity threshold: 2")
        self.assertContains(response, "Total by crossing type")
        self.assertContains(response, "Wild Type: 1")
        self.assertContains(response, "Multiple: 1")
        self.assertContains(response, "Unknown: 1")
        self.assertContains(response, "Breakdown by protocol")
        self.assertContains(response, "Procedure 701")
        self.assertContains(response, "Breakdown by procedure")
        self.assertContains(response, "Surgery")
        self.assertLess(
            content.index("Breakdown by protocol"),
            content.index("Breakdown by procedure"),
        )

    def test_home_office_return_includes_non_recovery_in_all_breakdowns(self):
        mouse = Mouse.objects.create(
            mouse_id="REPORT-NON-RECOVERY",
            protocol=self.protocol_1,
            protocol_start_date=date(self.current_year, 1, 1),
            severity="Non-recovery",
        )
        Procedure.objects.create(mouse=mouse, procedure_type=self.surgery)

        for output_mode in ("table", "text"):
            with self.subTest(output_mode=output_mode):
                response = self.client.get(self.home_office_query(output_mode))
                self.assertEqual(response.status_code, 200)
                report = response.context["home_office_report"]
                protocol = next(row for row in report["protocol_rows"] if row["protocol"] == self.protocol_1)
                procedure = next(row for row in report["procedure_rows"] if row["procedure_type"] == self.surgery)
                for counts in (report["total"], protocol, procedure):
                    self.assertEqual(counts["non-recovery"], 1)
                    self.assertEqual(counts["severity_rows"], [
                        {"label": label, "count": counts[value.lower()]}
                        for value, label in MAX_SEVERITY_CHOICES
                    ])
                self.assertEqual(report["total"]["total"], 5)
                if output_mode == "table":
                    for _, label in MAX_SEVERITY_CHOICES:
                        headers = Markup(response.content.decode()).find("th", scope="col")
                        self.assertEqual(sum(header.text.strip() == label for header in headers), 3)
                else:
                    self.assertContains(response, "Non-recovery: 1", count=3)

    def test_home_office_return_counts_legacy_double_triple_as_multiple(self):
        Mouse.objects.create(
            mouse_id="REPORT-LEGACY-DOUBLE-TRIPLE",
            protocol=self.protocol_1,
            protocol_start_date=date(self.current_year, 1, 11),
            crossing_type="Double_triple",
        )

        response = self.client.get(self.home_office_query())
        report = response.context["home_office_report"]
        total_crossing_type_rows = {
            row["label"]: row["count"]
            for row in report["total_crossing_type_rows"]
        }

        self.assertEqual(response.status_code, 200)
        self.assertEqual(total_crossing_type_rows["Multiple"], 2)
        self.assertNotIn("Crossing", total_crossing_type_rows)
