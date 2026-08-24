from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .admin import ProjectAdmin
from .management.commands.import_mice import Command as ImportMiceCommand
from .models import (
    Crossing,
    CrossingLine,
    Experiment,
    Mouse,
    MouseGenotype,
    MouseLine,
    Procedure,
    ProcedureType,
    Project,
    Protocol,
    Recording,
    RecordingField,
    RecordingType,
    Species,
    resolve_crossing,
)


class AuthenticatedTestCase(TestCase):
    def _pre_setup(self):
        super()._pre_setup()
        self.user = get_user_model().objects.create_user(
            username=f"user-{self._testMethodName}",
            password="password",
        )
        self.client.force_login(self.user)


def create_recording(*, mouse=None, mice=None, **kwargs):
    if mouse is not None:
        if mice is not None:
            raise ValueError("Use either mouse or mice, not both.")

        mice = [mouse]

    recording = Recording.objects.create(**kwargs)

    if mice:
        recording.mice.set(mice)

    return recording


def create_test_crossing(
    line_names=("Gad2",),
    species_name="Mouse",
    wild_type_names=(),
):
    species, _ = Species.objects.get_or_create(name=species_name)
    lines = []

    for line_name in line_names:
        line, _ = MouseLine.objects.get_or_create(
            species=species,
            name=line_name,
            defaults={"is_wild_type": line_name in wild_type_names},
        )
        lines.append(line)

    crossing, _ = resolve_crossing(species, lines)
    return crossing, lines


def crossing_post_data(crossing, lines, zygosity="het"):
    data = {
        "crossing_definition": str(crossing.pk),
        "crossing_search": f"{crossing.species.name} - {crossing.display_name}",
    }

    for line in lines:
        data[f"genotype_line_{line.pk}"] = zygosity

    return data


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class AuthenticationTests(TestCase):
    def test_lab_pages_require_login(self):
        response = self.client.get(reverse("lab:projects_page"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            "/login/?next=/projects/",
        )

    def test_user_can_log_in_and_out(self):
        User = get_user_model()
        User.objects.create_user(
            username="login-user",
            password="password",
        )

        response = self.client.post(
            reverse("lab:login"),
            {
                "username": "login-user",
                "password": "password",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/")

        response = self.client.post(reverse("lab:logout"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/login/")

    def test_staff_home_page_shows_admin_console_card(self):
        User = get_user_model()
        staff_user = User.objects.create_user(
            username="staff-user",
            password="password",
            is_staff=True,
        )
        self.client.force_login(staff_user)

        response = self.client.get(reverse("lab:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Admin Console")
        self.assertContains(response, 'href="/admin/"')
        self.assertContains(response, "Lines &amp; Crossings")
        self.assertContains(response, 'href="/lines-crossings/"')

    def test_normal_home_page_hides_admin_console_card(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="normal-user",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("lab:home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Admin Console")
        self.assertNotContains(response, "Lines &amp; Crossings")


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class ProjectAdminTests(TestCase):
    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser(
            username="project-admin",
            password="password",
        )
        self.client.force_login(self.admin_user)
        self.services_project = Project.objects.get(name="Services")

    def test_admin_delete_view_forbids_builtin_services_project(self):
        response = self.client.post(
            reverse("admin:lab_project_delete", args=[self.services_project.pk]),
            {
                "post": "yes",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            Project.objects.filter(pk=self.services_project.pk).exists()
        )

    def test_admin_bulk_delete_forbids_builtin_services_project(self):
        project_admin = ProjectAdmin(Project, AdminSite())

        with self.assertRaises(PermissionDenied):
            project_admin.delete_queryset(
                None,
                Project.objects.filter(pk=self.services_project.pk),
            )

        self.assertTrue(
            Project.objects.filter(pk=self.services_project.pk).exists()
        )

    def test_admin_can_delete_regular_project(self):
        project = Project.objects.create(name="Temporary admin project")

        response = self.client.post(
            reverse("admin:lab_project_delete", args=[project.pk]),
            {
                "post": "yes",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Project.objects.filter(pk=project.pk).exists())


class StructuredCrossingModelTests(TestCase):
    def test_creating_line_creates_single_line_crossing(self):
        species, _ = Species.objects.get_or_create(name="Mouse")
        line = MouseLine.objects.create(species=species, name="Gad2")

        crossing = Crossing.objects.get(
            species=species,
            canonical_key=str(line.pk),
        )
        self.assertEqual(crossing.display_name, "Gad2")
        self.assertEqual(crossing.inferred_crossing_type, "Single")
        self.assertEqual(list(crossing.lines.all()), [line])

    def test_line_names_are_unique_per_species_case_insensitive(self):
        mouse_species, _ = Species.objects.get_or_create(name="Mouse")
        rat_species = Species.objects.create(name="Rat")
        MouseLine.objects.create(species=mouse_species, name="Gad2")

        duplicate_line = MouseLine(species=mouse_species, name="gad2")

        with self.assertRaises(ValidationError):
            duplicate_line.full_clean()

        MouseLine.objects.create(species=rat_species, name="Gad2")
        self.assertEqual(MouseLine.objects.filter(name__iexact="gad2").count(), 2)

    def test_crossing_resolution_ignores_line_order(self):
        crossing, lines = create_test_crossing(("Mut1", "Mut2"))
        reused_crossing, created = resolve_crossing(
            crossing.species,
            list(reversed(lines)),
        )

        self.assertFalse(created)
        self.assertEqual(reused_crossing, crossing)
        self.assertEqual(crossing.canonical_key, reused_crossing.canonical_key)

    def test_crossing_line_rejects_mixed_species(self):
        crossing, _ = create_test_crossing(("Mut1",))
        rat_species = Species.objects.create(name="Rat")
        rat_line = MouseLine.objects.create(
            species=rat_species,
            name="RatLine",
        )
        crossing_line = CrossingLine(crossing=crossing, line=rat_line)

        with self.assertRaises(ValidationError):
            crossing_line.full_clean()

    def test_crossing_resolution_enforces_max_five_lines(self):
        species, _ = Species.objects.get_or_create(name="Mouse")
        lines = [
            MouseLine.objects.create(species=species, name=f"Mut{index}")
            for index in range(6)
        ]

        with self.assertRaises(ValidationError):
            resolve_crossing(species, lines)

    def test_inferred_crossing_type_counts_mutant_lines(self):
        wt_crossing, _ = create_test_crossing(("C57BL6",), wild_type_names=("C57BL6",))
        single_crossing, _ = create_test_crossing(("Mut1",))
        double_crossing, _ = create_test_crossing(("Mut2", "Mut3"))

        self.assertEqual(wt_crossing.inferred_crossing_type, "WT")
        self.assertEqual(single_crossing.inferred_crossing_type, "Single")
        self.assertEqual(double_crossing.inferred_crossing_type, "Multiple")


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class LinesCrossingsPageTests(AuthenticatedTestCase):
    def setUp(self):
        self.url = reverse("lab:lines_crossings_page")
        self.species = Species.objects.get(name="Mouse")
        self.staff_user = get_user_model().objects.create_user(
            username="lines-crossings-staff",
            password="password",
            is_staff=True,
        )

    def login_staff(self):
        self.client.force_login(self.staff_user)

    def test_normal_user_cannot_access_lines_crossings_page(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_staff_can_access_lines_crossings_page(self):
        self.login_staff()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lines & Crossings")
        self.assertNotContains(response, "Add Species")
        self.assertContains(response, "Add Lines")
        self.assertContains(response, "Create Crossing")

    def test_page_lists_species_created_in_admin(self):
        self.login_staff()
        Species.objects.create(name="Rat")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mouse")
        self.assertContains(response, "Rat")

    def test_staff_can_add_comma_separated_lines_and_skip_duplicates(self):
        self.login_staff()

        response = self.client.post(
            self.url,
            {
                "action": "add_lines",
                "species": str(self.species.pk),
                "line_names": "Gad2, Cdh23, gad2",
                "is_wild_type": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            MouseLine.objects.filter(species=self.species, name="Gad2").exists()
        )
        self.assertTrue(
            MouseLine.objects.filter(species=self.species, name="Cdh23").exists()
        )
        self.assertEqual(
            MouseLine.objects.filter(species=self.species).count(),
            2,
        )
        self.assertTrue(
            Crossing.objects.filter(
                species=self.species,
                display_name="Gad2",
                lines__name="Gad2",
            ).exists()
        )
        self.assertTrue(
            Crossing.objects.filter(
                species=self.species,
                display_name="Cdh23",
                lines__name="Cdh23",
            ).exists()
        )

    def test_line_lookup_is_scoped_by_species(self):
        self.login_staff()
        rat_species = Species.objects.create(name="Rat")
        mouse_line = MouseLine.objects.create(
            species=self.species,
            name="Gad2",
        )
        MouseLine.objects.create(species=rat_species, name="Gad2Rat")

        response = self.client.get(
            reverse("lab:line_lookup"),
            {
                "species": self.species.pk,
                "q": "Gad",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["results"],
            [
                {
                    "id": mouse_line.pk,
                    "name": mouse_line.name,
                    "label": "Gad2 (Mouse)",
                    "species_id": self.species.pk,
                }
            ],
        )

    def test_creating_duplicate_crossing_reuses_existing_crossing(self):
        self.login_staff()
        line_a = MouseLine.objects.create(species=self.species, name="Mut1")
        line_b = MouseLine.objects.create(species=self.species, name="Mut2")

        first_response = self.client.post(
            self.url,
            {
                "action": "create_crossing",
                "species": str(self.species.pk),
                "selected_lines": f"{line_a.pk},{line_b.pk}",
            },
        )
        second_response = self.client.post(
            self.url,
            {
                "action": "create_crossing",
                "species": str(self.species.pk),
                "selected_lines": f"{line_b.pk},{line_a.pk}",
            },
        )

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(
            Crossing.objects.filter(
                species=self.species,
                display_name="Mut1 x Mut2",
            ).count(),
            1,
        )

    def test_staff_can_delete_unused_line(self):
        self.login_staff()
        line = MouseLine.objects.create(species=self.species, name="Unused line")
        crossing = Crossing.objects.get(
            species=self.species,
            canonical_key=str(line.pk),
        )

        response = self.client.post(
            self.url,
            {
                "action": "delete_line",
                "line_id": line.pk,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(MouseLine.objects.filter(pk=line.pk).exists())
        self.assertFalse(Crossing.objects.filter(pk=crossing.pk).exists())
        self.assertContains(response, "Line Unused line was deleted.")

    def test_staff_cannot_delete_line_used_by_crossing(self):
        self.login_staff()
        crossing, lines = create_test_crossing(("Protected line A", "Protected line B"))

        response = self.client.post(
            self.url,
            {
                "action": "delete_line",
                "line_id": lines[0].pk,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(MouseLine.objects.filter(pk=lines[0].pk).exists())
        self.assertTrue(Crossing.objects.filter(pk=crossing.pk).exists())
        self.assertContains(
            response,
            (
                "Line Protected line A cannot be deleted because it is linked "
                "to 1 crossing."
            ),
        )

    def test_staff_cannot_delete_line_with_genotype_records(self):
        self.login_staff()
        line = MouseLine.objects.create(species=self.species, name="Genotyped line")
        protocol = Protocol.objects.create(
            protocol_number=906,
            licence_reference="licence-906",
            name="Protocol 906",
        )
        mouse = Mouse.objects.create(
            mouse_id="LINE-GENOTYPE-PROTECTED",
            protocol=protocol,
        )
        MouseGenotype.objects.create(
            mouse=mouse,
            mouse_line=line,
            zygosity="het",
        )

        response = self.client.post(
            self.url,
            {
                "action": "delete_line",
                "line_id": line.pk,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(MouseLine.objects.filter(pk=line.pk).exists())
        self.assertContains(
            response,
            (
                "Line Genotyped line cannot be deleted because it is linked to "
                "1 mouse genotype record."
            ),
        )

    def test_staff_can_delete_unused_crossing(self):
        self.login_staff()
        crossing, lines = create_test_crossing(("Unused crossing",))

        response = self.client.post(
            self.url,
            {
                "action": "delete_crossing",
                "crossing_id": crossing.pk,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Crossing.objects.filter(pk=crossing.pk).exists())
        self.assertTrue(MouseLine.objects.filter(pk=lines[0].pk).exists())
        self.assertContains(response, "Crossing Unused crossing was deleted.")

    def test_staff_cannot_delete_crossing_used_by_mouse(self):
        self.login_staff()
        crossing, _ = create_test_crossing(("Protected crossing",))
        protocol = Protocol.objects.create(
            protocol_number=907,
            licence_reference="licence-907",
            name="Protocol 907",
        )
        Mouse.objects.create(
            mouse_id="CROSSING-PROTECTED",
            protocol=protocol,
            crossing_definition=crossing,
        )

        response = self.client.post(
            self.url,
            {
                "action": "delete_crossing",
                "crossing_id": crossing.pk,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Crossing.objects.filter(pk=crossing.pk).exists())
        self.assertContains(
            response,
            (
                "Crossing Protected crossing cannot be deleted because "
                "1 mouse uses it."
            ),
        )


class ImportMiceCommandTests(SimpleTestCase):
    def test_command_line_options_override_defaults(self):
        command = ImportMiceCommand()
        parser = command.create_parser("manage.py", "import_mice")
        options = parser.parse_args(
            [
                "--excel-file",
                "/tmp/mice.xlsx",
                "--sheet-name",
                "Protocol 5",
                "--protocol-number",
                "5",
                "--header-row",
                "2",
            ]
        )

        self.assertEqual(options.excel_file, Path("/tmp/mice.xlsx"))
        self.assertEqual(options.sheet_name, "Protocol 5")
        self.assertEqual(options.protocol_number, 5)
        self.assertEqual(options.header_row, 2)

    def test_handle_uses_protocol_number_option(self):
        command = ImportMiceCommand()

        with TemporaryDirectory() as temporary_directory:
            excel_file = Path(temporary_directory) / "mice.xlsx"
            excel_file.touch()

            with patch(
                "lab.management.commands.import_mice.Protocol.objects.get",
                side_effect=Protocol.DoesNotExist,
            ) as get_protocol:
                with self.assertRaisesMessage(
                    CommandError,
                    "protocol_number=987654",
                ):
                    command.handle(
                        excel_file=excel_file,
                        sheet_name="Protocol 987654",
                        protocol_number=987654,
                        header_row=0,
                    )

                get_protocol.assert_called_once_with(
                    protocol_number=987654,
                )

    def test_source_mouse_id_uses_last_slash_separated_value(self):
        command = ImportMiceCommand()

        self.assertEqual(
            command.source_mouse_id("1_M_LE / 411859"),
            "411859",
        )
        self.assertEqual(
            command.source_mouse_id("411859"),
            "411859",
        )


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class ProcedurePageLicenceTests(AuthenticatedTestCase):
    def setUp(self):
        Protocol.objects.all().delete()

        self.current_year = timezone.localdate().year
        self.previous_year = self.current_year - 1
        self.licence_a = "licence-a"
        self.licence_b = "licence-b"
        self.protocol_a1 = Protocol.objects.create(
            protocol_number=201,
            licence_reference=self.licence_a,
            name="Protocol 201",
        )
        self.protocol_a2 = Protocol.objects.create(
            protocol_number=202,
            licence_reference=self.licence_a,
            name="Protocol 202",
        )
        self.protocol_b1 = Protocol.objects.create(
            protocol_number=301,
            licence_reference=self.licence_b,
            name="Protocol 301",
        )
        self.procedure_type = ProcedureType.objects.create(
            name="Tattooing",
        )
        self.mouse_a1 = Mouse.objects.create(
            mouse_id="MOUSE-P201",
            protocol=self.protocol_a1,
            protocol_start_date=date(self.current_year, 1, 10),
            crossing="Line A",
            crossing_type="WT",
        )
        self.mouse_a2 = Mouse.objects.create(
            mouse_id="MOUSE-P202",
            protocol=self.protocol_a2,
            protocol_start_date=date(self.current_year, 1, 11),
        )
        self.mouse_b1 = Mouse.objects.create(
            mouse_id="MOUSE-P301",
            protocol=self.protocol_b1,
            protocol_start_date=date(self.current_year, 1, 12),
        )
        self.old_mouse_a1 = Mouse.objects.create(
            mouse_id="MOUSE-P201-OLD",
            protocol=self.protocol_a1,
            protocol_start_date=date(self.previous_year, 1, 10),
        )

    def test_procedure_page_links_to_mouse_ids_containing_slashes(self):
        imported_mouse = Mouse.objects.create(
            mouse_id="1_M_LE / 411859",
            protocol=self.protocol_a1,
            protocol_start_date=date(self.current_year, 1, 13),
        )
        mouse_record_url = reverse(
            "lab:mouse_record",
            args=[imported_mouse.pk],
        )

        response = self.client.get(
            reverse(
                "lab:procedure_page_licence_protocol",
                kwargs={
                    "licence_reference": self.protocol_a1.licence_reference,
                    "protocol_number": self.protocol_a1.protocol_number,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, imported_mouse.mouse_id)
        self.assertContains(response, mouse_record_url)

    def test_procedure_root_redirects_to_first_licence(self):
        response = self.client.get(reverse("lab:procedure_page"))

        self.assertRedirects(
            response,
            reverse(
                "lab:procedure_page_licence",
                kwargs={"licence_reference": self.licence_a},
            ),
            fetch_redirect_response=False,
        )

    def test_licence_page_defaults_to_first_protocol(self):
        response = self.client.get(
            reverse(
                "lab:procedure_page_licence",
                kwargs={"licence_reference": self.licence_a},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["selected_licence_reference"],
            self.licence_a,
        )
        self.assertEqual(response.context["selected_protocol"], self.protocol_a1)
        self.assertEqual(
            [
                protocol.pk
                for protocol in response.context["protocols"]
            ],
            [self.protocol_a1.pk, self.protocol_a2.pk],
        )
        self.assertContains(response, "Protocol 201")
        self.assertContains(response, "Protocol 202")
        self.assertNotContains(response, "Protocol 301")

    def test_protocol_page_shows_mice_for_selected_protocol_only(self):
        response = self.client.get(
            reverse(
                "lab:procedure_page_licence_protocol",
                kwargs={
                    "licence_reference": self.licence_a,
                    "protocol_number": self.protocol_a2.protocol_number,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_protocol"], self.protocol_a2)
        self.assertContains(response, self.mouse_a2.mouse_id)
        self.assertNotContains(response, self.mouse_a1.mouse_id)
        self.assertNotContains(response, self.mouse_b1.mouse_id)

    def test_protocol_page_hides_unused_procedure_columns(self):
        unused_procedure_type = ProcedureType.objects.create(
            name="Unused procedure",
        )
        Procedure.objects.create(
            mouse=self.mouse_a1,
            procedure_type=self.procedure_type,
            date=date(self.current_year, 7, 1),
        )
        Procedure.objects.create(
            mouse=self.mouse_b1,
            procedure_type=unused_procedure_type,
            date=date(self.current_year, 7, 1),
        )

        response = self.client.get(
            reverse(
                "lab:procedure_page_licence_protocol",
                kwargs={
                    "licence_reference": self.licence_a,
                    "protocol_number": self.protocol_a1.protocol_number,
                },
            )
        )
        column_labels = [
            column.get("label")
            for column in response.context["table"]["columns"]
        ]

        self.assertEqual(response.status_code, 200)
        self.assertIn("Tattooing", column_labels)
        self.assertNotIn("Unused procedure", column_labels)

    def test_protocol_page_renders_select_all_and_page_size_controls(self):
        response = self.client.get(
            reverse(
                "lab:procedure_page_licence_protocol",
                kwargs={
                    "licence_reference": self.protocol_a1.licence_reference,
                    "protocol_number": self.protocol_a1.protocol_number,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_size_control"]["current"], 25)
        self.assertContains(response, "Select all")
        self.assertContains(response, "data-table-select-all-button")
        self.assertContains(response, 'name="per_page"')
        for page_size in (25, 50, 100, 150, 200):
            self.assertContains(response, f'value="{page_size}"')

    def test_protocol_page_honours_page_size(self):
        for index in range(30):
            Mouse.objects.create(
                mouse_id=f"MOUSE-P201-PAGE-SIZE-{index}",
                protocol=self.protocol_a1,
                protocol_start_date=date(self.current_year, 1, 20),
            )

        response = self.client.get(
            reverse(
                "lab:procedure_page_licence_protocol",
                kwargs={
                    "licence_reference": self.protocol_a1.licence_reference,
                    "protocol_number": self.protocol_a1.protocol_number,
                },
            )
            + "?per_page=50"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["record_count"]["end"], 31)
        self.assertEqual(response.context["page_size_control"]["current"], 50)

    def test_protocol_page_combines_crossing_and_crossing_type(self):
        Mouse.objects.create(
            mouse_id="MOUSE-P201-BLANK-CROSSING",
            protocol=self.protocol_a1,
            protocol_start_date=date(self.current_year, 1, 13),
        )
        response = self.client.get(
            reverse(
                "lab:procedure_page_licence_protocol",
                kwargs={
                    "licence_reference": self.licence_a,
                    "protocol_number": self.protocol_a1.protocol_number,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Crossing/type")
        self.assertContains(response, "Line A<br>Wild Type", html=True)
        self.assertContains(response, "n/a<br>n/a", html=True)

    def test_current_year_filter_is_checked_and_default_on(self):
        response = self.client.get(
            reverse(
                "lab:procedure_page_licence_protocol",
                kwargs={
                    "licence_reference": self.licence_a,
                    "protocol_number": self.protocol_a1.protocol_number,
                },
            )
        )
        current_year_field = next(
            field
            for field in response.context["filter_form"]["fields"]
            if field["name"] == "show_current_year"
        )

        self.assertTrue(current_year_field["checked"])
        self.assertContains(response, self.mouse_a1.mouse_id)
        self.assertNotContains(response, self.old_mouse_a1.mouse_id)

    def test_crossing_filter_uses_crossing_autocomplete(self):
        response = self.client.get(
            reverse(
                "lab:procedure_page_licence_protocol",
                kwargs={
                    "licence_reference": self.licence_a,
                    "protocol_number": self.protocol_a1.protocol_number,
                },
            )
        )
        crossing_field = next(
            field
            for field in response.context["filter_form"]["fields"]
            if field["name"] == "crossing"
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["filter_form"]["has_autocomplete"])
        self.assertEqual(
            crossing_field["autocomplete"],
            {
                "url": reverse("lab:crossing_lookup"),
                "value_key": "display_name",
                "label_key": "label",
            },
        )
        self.assertContains(response, 'data-filter-autocomplete-input="true"')
        self.assertContains(
            response,
            f'data-autocomplete-url="{reverse("lab:crossing_lookup")}"',
        )
        self.assertContains(response, 'data-autocomplete-value-key="display_name"')

    def test_unchecked_current_year_filter_shows_all_years(self):
        response = self.client.get(
            reverse(
                "lab:procedure_page_licence_protocol",
                kwargs={
                    "licence_reference": self.licence_a,
                    "protocol_number": self.protocol_a1.protocol_number,
                },
            )
            + "?show_current_year=0"
        )
        current_year_field = next(
            field
            for field in response.context["filter_form"]["fields"]
            if field["name"] == "show_current_year"
        )

        self.assertFalse(current_year_field["checked"])
        self.assertContains(response, self.mouse_a1.mouse_id)
        self.assertContains(response, self.old_mouse_a1.mouse_id)

    def test_current_year_filter_includes_current_year_procedure_date(self):
        old_protocol_mouse = Mouse.objects.create(
            mouse_id="MOUSE-P201-CURRENT-PROCEDURE",
            protocol=self.protocol_a1,
            protocol_start_date=date(self.previous_year, 2, 1),
        )
        Procedure.objects.create(
            mouse=old_protocol_mouse,
            procedure_type=self.procedure_type,
            date=date(self.current_year, 3, 1),
        )

        response = self.client.get(
            reverse(
                "lab:procedure_page_licence_protocol",
                kwargs={
                    "licence_reference": self.licence_a,
                    "protocol_number": self.protocol_a1.protocol_number,
                },
            )
        )

        self.assertContains(response, old_protocol_mouse.mouse_id)

    def test_severity_exceeded_filter_shows_only_exceeded_mice(self):
        self.protocol_a1.max_severity = "Mild"
        self.protocol_a1.save(update_fields=["max_severity"])
        exceeded_mouse = Mouse.objects.create(
            mouse_id="MOUSE-P201-SEVERITY-EXCEEDED",
            protocol=self.protocol_a1,
            protocol_start_date=date(self.current_year, 1, 15),
            severity="Moderate",
        )
        allowed_mouse = Mouse.objects.create(
            mouse_id="MOUSE-P201-SEVERITY-OK",
            protocol=self.protocol_a1,
            protocol_start_date=date(self.current_year, 1, 16),
            severity="Mild",
        )

        response = self.client.get(
            reverse(
                "lab:procedure_page_licence_protocol",
                kwargs={
                    "licence_reference": self.licence_a,
                    "protocol_number": self.protocol_a1.protocol_number,
                },
            )
            + "?severity_exceeded=1"
        )
        severity_field = next(
            field
            for field in response.context["filter_form"]["fields"]
            if field["name"] == "severity_exceeded"
        )

        self.assertEqual(severity_field["value"], "1")
        self.assertContains(response, exceeded_mouse.mouse_id)
        self.assertNotContains(response, allowed_mouse.mouse_id)

    def test_protocol_page_shows_latest_procedure_and_duplicate_count(self):
        Procedure.objects.create(
            mouse=self.mouse_a1,
            procedure_type=self.procedure_type,
            date=date(self.current_year, 7, 1),
            lab_member="OLD",
            lab_book_page="1",
        )
        Procedure.objects.create(
            mouse=self.mouse_a1,
            procedure_type=self.procedure_type,
            date=date(self.current_year, 7, 22),
            lab_member="NEW",
            lab_book_page="2",
        )

        response = self.client.get(
            reverse(
                "lab:procedure_page_licence_protocol",
                kwargs={
                    "licence_reference": self.licence_a,
                    "protocol_number": self.protocol_a1.protocol_number,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2 procedures total")
        self.assertContains(response, "NEW")
        self.assertContains(response, "Page 2")
        self.assertNotContains(response, "OLD")
        self.assertNotContains(response, "Page 1")

    def test_protocol_page_marks_procedure_checkmarks_when_severity_exceeded(self):
        self.protocol_a1.max_severity = "Mild"
        self.protocol_a1.save(update_fields=["max_severity"])
        exceeded_mouse = Mouse.objects.create(
            mouse_id="MOUSE-P201-RED-CHECK",
            protocol=self.protocol_a1,
            protocol_start_date=date(self.current_year, 1, 15),
            severity="Severe",
        )
        Procedure.objects.create(
            mouse=exceeded_mouse,
            procedure_type=self.procedure_type,
            date=date(self.current_year, 7, 1),
        )

        response = self.client.get(
            reverse(
                "lab:procedure_page_licence_protocol",
                kwargs={
                    "licence_reference": self.licence_a,
                    "protocol_number": self.protocol_a1.protocol_number,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, exceeded_mouse.mouse_id)
        self.assertContains(response, "checkmark checkmark-danger")

    def test_page_actions_use_licence_scoped_urls(self):
        response = self.client.get(
            reverse(
                "lab:procedure_page_licence_protocol",
                kwargs={
                    "licence_reference": self.licence_a,
                    "protocol_number": self.protocol_a1.protocol_number,
                },
            )
        )
        add_mouse_url = reverse(
            "lab:add_mouse_licence_protocol",
            kwargs={
                "licence_reference": self.licence_a,
                "protocol_number": self.protocol_a1.protocol_number,
            },
        )
        add_litter_url = reverse(
            "lab:add_litter_licence_protocol",
            kwargs={
                "licence_reference": self.licence_a,
                "protocol_number": self.protocol_a1.protocol_number,
            },
        )
        edit_selected_url = reverse(
            "lab:edit_selected_mice_licence_protocol",
            kwargs={
                "licence_reference": self.licence_a,
                "protocol_number": self.protocol_a1.protocol_number,
            },
        )

        self.assertContains(response, f'href="{add_mouse_url}"')
        self.assertContains(response, f'href="{add_litter_url}?return_url=')
        self.assertContains(response, f'action="{edit_selected_url}"')

    def test_add_mouse_scoped_protocol_cannot_be_changed(self):
        crossing, lines = create_test_crossing(("Scoped",))
        add_mouse_url = reverse(
            "lab:add_mouse_licence_protocol",
            kwargs={
                "licence_reference": self.protocol_a1.licence_reference,
                "protocol_number": self.protocol_a1.protocol_number,
            },
        )
        return_url = reverse(
            "lab:procedure_page_licence_protocol",
            kwargs={
                "licence_reference": self.protocol_a1.licence_reference,
                "protocol_number": self.protocol_a1.protocol_number,
            },
        )

        response = self.client.post(
            add_mouse_url,
            {
                "return_url": return_url,
                "mouse_id": "MOUSE-SCOPED-ADD",
                "date_of_birth": "",
                "tattoo": "",
                "genotype": "Scoped add",
                "crossing": "",
                "crossing_type": "",
                "sex": "",
                "status": "Alive",
                "protocol_start_date": "",
                "cull_date": "",
                "custom_metadata": "{}",
                "protocol": str(self.protocol_a2.pk),
                "severity": "",
                "notes": "",
                **crossing_post_data(crossing, lines),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], return_url)

        mouse = Mouse.objects.get(pk="MOUSE-SCOPED-ADD")
        self.assertEqual(mouse.protocol, self.protocol_a1)

    def test_legacy_protocol_url_redirects_to_licence_scoped_page(self):
        response = self.client.get(
            reverse(
                "lab:procedure_page_protocol",
                args=[self.protocol_a1.protocol_number],
            )
        )

        self.assertRedirects(
            response,
            reverse(
                "lab:procedure_page_licence_protocol",
                kwargs={
                    "licence_reference": self.licence_a,
                    "protocol_number": self.protocol_a1.protocol_number,
                },
            ),
            fetch_redirect_response=False,
        )


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class MicePageTests(AuthenticatedTestCase):
    def setUp(self):
        self.current_year = timezone.localdate().year
        self.protocol = Protocol.objects.create(
            protocol_number=601,
            licence_reference="licence-601",
            name="Protocol 601",
        )
        self.other_protocol = Protocol.objects.create(
            protocol_number=602,
            licence_reference="licence-602",
            name="Protocol 602",
        )
        self.non_regulated_protocol = Protocol.objects.create(
            protocol_number=603,
            licence_reference="licence-603",
            name="Non-regulated protocol",
            allows_regulated_procedures=False,
        )
        self.recent_mouse = Mouse.objects.create(
            mouse_id="MICE-RECENT",
            protocol=self.protocol,
            date_of_birth=date(self.current_year, 7, 1),
            genotype="Gad2",
            sex="F",
            status="Alive",
        )
        self.non_regulated_mouse = Mouse.objects.create(
            mouse_id="MICE-NON-REGULATED",
            protocol=self.non_regulated_protocol,
            date_of_birth=date(self.current_year - 1, 6, 1),
            genotype="NonRegulated",
            sex="M",
            status="Alive",
            cull_date=date(self.current_year, 8, 1),
        )
        self.old_mouse = Mouse.objects.create(
            mouse_id="MICE-OLD",
            protocol=self.other_protocol,
            date_of_birth=date(self.current_year - 2, 1, 1),
            genotype="Vglut",
            sex="M",
            status="Culled",
            cull_date=date(self.current_year - 1, 9, 1),
        )
        self.no_dob_mouse = Mouse.objects.create(
            mouse_id="MICE-NO-DOB",
            protocol=self.protocol,
            genotype="NoDob",
            status="Alive",
        )
        self.procedure_type = ProcedureType.objects.create(
            name="Mice page procedure",
        )
        self.mice_url = reverse("lab:mice_page")
        self.edit_selected_url = reverse("lab:edit_selected_mice_all")
        self.delete_selected_url = reverse("lab:delete_selected_mice_all")
        self.add_mouse_url = reverse("lab:add_mouse_all")
        self.add_litter_url = reverse("lab:add_litter_all")

    def test_reports_page_renders_active_mice_card(self):
        response = self.client.get(reverse("lab:reports_page"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mice")
        self.assertContains(response, f'href="{self.mice_url}"')

    def test_mice_page_renders_all_mice_including_non_regulated_protocol(self):
        response = self.client.get(self.mice_url)
        filter_names = [
            field["name"]
            for field in response.context["filter_form"]["fields"]
        ]

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("protocol", filter_names)
        self.assertNotIn("show_current_year", filter_names)
        self.assertContains(response, self.recent_mouse.mouse_id)
        self.assertContains(response, self.non_regulated_mouse.mouse_id)
        self.assertContains(response, self.old_mouse.mouse_id)
        self.assertContains(response, self.no_dob_mouse.mouse_id)
        self.assertEqual(response.context["record_count"]["count"], 4)

    def test_mice_page_defaults_to_dob_descending_with_empty_dob_last(self):
        response = self.client.get(self.mice_url)
        content = response.content.decode()

        self.assertLess(
            content.index(self.recent_mouse.mouse_id),
            content.index(self.non_regulated_mouse.mouse_id),
        )
        self.assertLess(
            content.index(self.non_regulated_mouse.mouse_id),
            content.index(self.old_mouse.mouse_id),
        )
        self.assertLess(
            content.index(self.old_mouse.mouse_id),
            content.index(self.no_dob_mouse.mouse_id),
        )

    def test_mice_page_renders_admin_style_columns_and_actions(self):
        response = self.client.get(self.mice_url)

        self.assertEqual(response.status_code, 200)
        for label in (
            "Mouse ID",
            "Genotype",
            "Sex",
            "Status",
            "DOB",
            "Cull date",
            "Protocol",
        ):
            self.assertContains(response, label)

        self.assertContains(response, 'name="selected_mouse"')
        self.assertContains(response, self.edit_selected_url)
        self.assertContains(response, f'href="{self.add_mouse_url}?return_url=')
        self.assertContains(response, f'href="{self.add_litter_url}?return_url=')
        self.assertContains(response, "Add mouse")
        self.assertContains(response, "Add litter")
        self.assertNotContains(response, "Edit mouse")
        self.assertContains(response, "Select all")
        self.assertContains(response, "data-table-select-all-button")
        self.assertContains(response, 'name="per_page"')
        self.assertEqual(response.context["page_size_control"]["current"], 25)
        for page_size in (25, 50, 100, 150, 200):
            self.assertContains(response, f'value="{page_size}"')
        self.assertContains(
            response,
            reverse("lab:mouse_record", args=[self.recent_mouse.pk]),
        )

    def test_mice_page_honours_page_size(self):
        for index in range(30):
            Mouse.objects.create(
                mouse_id=f"MICE-PAGE-SIZE-{index}",
                protocol=self.protocol,
                date_of_birth=date(self.current_year, 1, 1),
            )

        response = self.client.get(f"{self.mice_url}?per_page=50")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["record_count"]["end"], 34)
        self.assertEqual(response.context["page_size_control"]["current"], 50)

    def test_mice_page_filters(self):
        cases = [
            ("mouse_id=MICE-RECENT", self.recent_mouse),
            ("genotype=NonRegulated", self.non_regulated_mouse),
            ("sex=F", self.recent_mouse),
            ("status=Culled", self.old_mouse),
            (
                f"dob={self.recent_mouse.date_of_birth:%Y-%m-%d}",
                self.recent_mouse,
            ),
            (
                f"cull_date={self.non_regulated_mouse.cull_date:%Y-%m-%d}",
                self.non_regulated_mouse,
            ),
        ]

        for query_string, expected_mouse in cases:
            with self.subTest(query_string=query_string):
                response = self.client.get(f"{self.mice_url}?{query_string}")

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, expected_mouse.mouse_id)
                self.assertEqual(response.context["record_count"]["count"], 1)

    def test_mice_page_ignores_removed_protocol_and_current_year_filters(self):
        response = self.client.get(
            f"{self.mice_url}?protocol={self.other_protocol.pk}"
            "&show_current_year=1"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.recent_mouse.mouse_id)
        self.assertContains(response, self.non_regulated_mouse.mouse_id)
        self.assertContains(response, self.old_mouse.mouse_id)
        self.assertContains(response, self.no_dob_mouse.mouse_id)
        self.assertEqual(response.context["record_count"]["count"], 4)

    def test_unscoped_add_mouse_requires_protocol(self):
        crossing, lines = create_test_crossing(("Added",))
        response = self.client.get(
            f"{self.add_mouse_url}?return_url={self.mice_url}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["selected_protocol"])
        self.assertContains(response, "Select a protocol before saving.")
        self.assertContains(response, "Local identifier")
        self.assertIsNone(response.context["form"].fields["protocol"].initial)

        response = self.client.post(
            self.add_mouse_url,
            {
                "return_url": self.mice_url,
                "mouse_id": "MICE-ADDED-NO-PROTOCOL",
                "date_of_birth": "",
                "tattoo": "",
                "genotype": "Added",
                "crossing": "",
                "crossing_type": "",
                "sex": "",
                "status": "Alive",
                "protocol_start_date": "",
                "cull_date": "",
                "custom_metadata": "{}",
                "protocol": str(self.non_regulated_protocol.pk),
                "severity": "",
                "notes": "",
                **crossing_post_data(crossing, lines),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.mice_url)

        mouse = Mouse.objects.get(pk="MICE-ADDED-NO-PROTOCOL")
        self.assertEqual(mouse.protocol, self.non_regulated_protocol)
        self.assertEqual(mouse.genotype, "Added: Het")

    def test_add_mouse_saves_structured_crossing_and_genotype_calls(self):
        crossing, lines = create_test_crossing(("Mut1", "Mut2"))

        response = self.client.post(
            self.add_mouse_url,
            {
                "return_url": self.mice_url,
                "mouse_id": "MICE-STRUCTURED-ADD",
                "date_of_birth": "",
                "tattoo": "",
                "breeding_pair": "",
                "sex": "",
                "status": "Alive",
                "protocol_start_date": "",
                "cull_date": "",
                "custom_metadata": "{}",
                "protocol": str(self.protocol.pk),
                "severity": "",
                "notes": "",
                "crossing_definition": str(crossing.pk),
                "crossing_search": f"Mouse - {crossing.display_name}",
                f"genotype_line_{lines[0].pk}": "het",
                f"genotype_line_{lines[1].pk}": "hom",
            },
        )

        self.assertEqual(response.status_code, 302)
        mouse = Mouse.objects.get(pk="MICE-STRUCTURED-ADD")
        self.assertEqual(mouse.crossing_definition, crossing)
        self.assertEqual(mouse.crossing, "Mut1 x Mut2")
        self.assertEqual(mouse.crossing_type, "Multiple")
        self.assertEqual(mouse.genotype, "Mut1: Het; Mut2: Hom")
        self.assertEqual(
            {
                genotype.mouse_line_id: genotype.zygosity
                for genotype in mouse.genotype_calls.all()
            },
            {
                lines[0].pk: "het",
                lines[1].pk: "hom",
            },
        )

    def test_add_mouse_auto_id_uses_structured_crossing_without_species_or_cross_separator(self):
        crossing, lines = create_test_crossing(("Mut1", "Mut2"))
        response = self.client.post(
            self.add_mouse_url,
            {
                "return_url": self.mice_url,
                "generate_mouse_id": "on",
                "mouse_id": "",
                "date_of_birth": "2026-01-02",
                "tattoo": "7",
                "breeding_pair": "Line A",
                "genotype": "Added",
                "crossing": "",
                "crossing_type": "",
                "sex": "",
                "status": "Alive",
                "protocol_start_date": "",
                "cull_date": "",
                "custom_metadata": "{}",
                "protocol": str(self.protocol.pk),
                "severity": "",
                "notes": "",
                **crossing_post_data(crossing, lines),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Mouse.objects.filter(mouse_id="20260102_mut1mut2_linea_7").exists()
        )

    def test_mouse_id_preview_uses_structured_crossing_without_species_or_cross_separator(self):
        crossing, _ = create_test_crossing(("Mut1", "Mut2"))

        response = self.client.get(
            reverse("lab:mouse_id_preview"),
            {
                "date_of_birth": "2026-01-02",
                "breeding_pair": "Line A",
                "tattoo": "7",
                "crossing_definition": str(crossing.pk),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "base_id": "20260102_mut1mut2_linea_7",
                "final_id": "20260102_mut1mut2_linea_7",
            },
        )

    def test_add_mouse_dynamic_genotype_subset_script_is_available(self):
        response = self.client.get(self.add_mouse_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Genotype")
        self.assertContains(response, "data-genotype-subset")

    def test_unscoped_add_litter_requires_protocol(self):
        response = self.client.get(
            f"{self.add_litter_url}?return_url={self.mice_url}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["selected_protocol"])
        self.assertContains(response, "Select a protocol before saving.")
        self.assertIsNone(response.context["form"].fields["protocol"].initial)
        self.assertFalse(response.context["can_add_litter_procedure"])
        self.assertContains(
            response,
            "Select a protocol that allows regulated procedures before adding procedures.",
        )
        self.assertContains(response, "data-procedure-fieldset")
        self.assertContains(response, "disabled")

        crossing, lines = create_test_crossing(("Unassigned litter",))
        response = self.client.post(
            self.add_litter_url,
            {
                "return_url": self.mice_url,
                "mouse_count": "2",
                "tattoo_start": "20",
                "use_progressive_mouse_ids": "on",
                "mouse_id_prefix": "LITTER-",
                "mouse_id_start": "30",
                "date_of_birth": "",
                "sex": "",
                "status": "Alive",
                "protocol": str(self.protocol.pk),
                "protocol_start_date": "",
                "cull_date": "",
                "severity": "",
                "notes": "No protocol litter",
                "procedure_type": str(self.procedure_type.pk),
                "procedure_date": "2026-07-29",
                "procedure_lab_member": "AB",
                "procedure_lab_book_page": "10",
                **crossing_post_data(crossing, lines),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.mice_url)

        mice = list(
            Mouse.objects
            .filter(mouse_id__in=["LITTER-30", "LITTER-31"])
            .order_by("mouse_id")
        )
        self.assertEqual(len(mice), 2)
        self.assertTrue(all(mouse.protocol == self.protocol for mouse in mice))
        self.assertTrue(
            all(mouse.genotype == "Unassigned litter: Het" for mouse in mice)
        )
        self.assertEqual(
            Procedure.objects.filter(
                mouse__in=mice,
                procedure_type=self.procedure_type,
            ).count(),
            2,
        )

    def test_unscoped_edit_selected_rejects_no_selection(self):
        response = self.client.post(
            self.edit_selected_url,
            {
                "return_url": self.mice_url,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.mice_url)

    def test_unscoped_edit_selected_single_mouse_opens_mouse_edit(self):
        response = self.client.post(
            self.edit_selected_url,
            {
                "return_url": self.mice_url,
                "selected_mouse": [self.non_regulated_mouse.pk],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response["Location"].startswith(
                reverse("lab:edit_mouse", args=[self.non_regulated_mouse.pk])
            )
        )
        self.assertIn("return_url=", response["Location"])

    def test_unscoped_edit_selected_opens_for_mixed_protocol_mice(self):
        response = self.client.post(
            self.edit_selected_url,
            {
                "return_url": self.mice_url,
                "selected_mouse": [
                    self.recent_mouse.pk,
                    self.old_mouse.pk,
                    self.non_regulated_mouse.pk,
                ],
            },
        )

        self.assertEqual(response.status_code, 302)
        response = self.client.get(response["Location"])

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "All mice.")
        self.assertContains(response, self.recent_mouse.mouse_id)
        self.assertContains(response, self.old_mouse.mouse_id)
        self.assertContains(response, self.non_regulated_mouse.mouse_id)
        self.assertContains(
            response,
            "Regulated procedure creation is disabled for one or more selected mice.",
        )
        self.assertFalse(response.context["can_add_bulk_procedure"])
        self.assertContains(response, "<fieldset")
        self.assertContains(response, "disabled")
        self.assertContains(
            response,
            reverse("lab:delete_selected_mice_all"),
        )

    def test_unscoped_bulk_save_updates_mice_across_protocols(self):
        response = self.client.post(
            self.edit_selected_url,
            {
                "apply_bulk_edit": "1",
                "return_url": self.mice_url,
                "selected_mouse": [
                    self.recent_mouse.pk,
                    self.old_mouse.pk,
                    self.non_regulated_mouse.pk,
                ],
                "update_status": "on",
                "status": "Archived",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.mice_url)

        self.recent_mouse.refresh_from_db()
        self.old_mouse.refresh_from_db()
        self.non_regulated_mouse.refresh_from_db()
        self.no_dob_mouse.refresh_from_db()
        self.assertEqual(self.recent_mouse.status, "Archived")
        self.assertEqual(self.old_mouse.status, "Archived")
        self.assertEqual(self.non_regulated_mouse.status, "Archived")
        self.assertEqual(self.no_dob_mouse.status, "Alive")

    def test_unscoped_delete_selected_removes_mixed_protocol_mice(self):
        response = self.client.post(
            self.delete_selected_url,
            {
                "return_url": self.mice_url,
                "selected_mouse": [
                    self.recent_mouse.pk,
                    self.non_regulated_mouse.pk,
                ],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(self.delete_selected_url, response["Location"])

        response = self.client.post(
            response["Location"],
            {
                "return_url": self.mice_url,
                "selected_mouse": [
                    self.recent_mouse.pk,
                    self.non_regulated_mouse.pk,
                ],
                "confirm_delete": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.mice_url)
        self.assertFalse(
            Mouse.objects.filter(pk=self.recent_mouse.pk).exists()
        )
        self.assertFalse(
            Mouse.objects.filter(pk=self.non_regulated_mouse.pk).exists()
        )
        self.assertTrue(
            Mouse.objects.filter(pk=self.old_mouse.pk).exists()
        )


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class ProjectsPageTests(AuthenticatedTestCase):
    def setUp(self):
        self.projects_url = reverse("lab:projects_page")
        self.add_project_url = reverse("lab:add_project")
        self.active_project = Project.objects.create(
            name="Auditory Cortex Mapping",
            lab_member="Alice",
            description="Two-photon mapping",
            start_date=date(2026, 1, 10),
            end_date=date(2026, 7, 30),
            active=True,
        )
        self.other_active_project = Project.objects.create(
            name="Vestibular Pilot",
            lab_member="Bob",
            description="Pilot recordings",
            start_date=date(2026, 2, 1),
            active=True,
        )
        self.inactive_project = Project.objects.create(
            name="Archived Calcium Imaging",
            lab_member="Alice",
            description="Completed analysis",
            start_date=date(2025, 3, 5),
            end_date=date(2025, 12, 20),
            active=False,
        )
        self.service_project = Project.objects.create(
            name="Sequencing Service",
            description="Shared lab service",
            active=True,
            is_service=True,
        )

    def test_home_page_renders_active_projects_card(self):
        response = self.client.get(reverse("lab:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Projects")
        self.assertContains(response, f'href="{self.projects_url}"')

    def test_home_page_renders_services_card(self):
        response = self.client.get(reverse("lab:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Services")
        self.assertContains(response, f'href="{reverse("lab:services_page")}"')

    def test_projects_page_defaults_to_active_projects(self):
        response = self.client.get(self.projects_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["status"], "active")
        self.assertContains(response, self.active_project.name)
        self.assertContains(response, self.other_active_project.name)
        self.assertNotContains(response, self.inactive_project.name)
        self.assertNotContains(response, self.service_project.name)
        self.assertEqual(response.context["record_count"]["count"], 2)

    def test_projects_page_excludes_service_projects(self):
        response = self.client.get(self.projects_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.service_project.name)
        self.assertNotContains(
            response,
            reverse("lab:edit_project", args=[self.service_project.pk]),
        )

    def test_projects_page_renders_columns_and_actions(self):
        response = self.client.get(self.projects_url)

        self.assertEqual(response.status_code, 200)
        for label in (
            "Name",
            "Lab Member",
            "Description",
            "Start Date",
            "End Date",
            "Actions",
        ):
            self.assertContains(response, label)

        self.assertContains(response, "Add a project")
        self.assertContains(
            response,
            f'href="{self.add_project_url}?return_url=',
        )
        self.assertContains(response, "Edit")
        self.assertContains(
            response,
            reverse("lab:edit_project", args=[self.active_project.pk]),
        )
        self.assertContains(
            response,
            reverse("lab:experiments_page", args=[self.active_project.pk]),
        )

    def test_projects_page_inactive_tab_shows_inactive_projects_only(self):
        response = self.client.get(f"{self.projects_url}?status=inactive")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["status"], "inactive")
        self.assertContains(response, self.inactive_project.name)
        self.assertNotContains(response, self.active_project.name)
        self.assertNotContains(response, self.other_active_project.name)
        self.assertNotContains(response, self.service_project.name)
        self.assertEqual(response.context["record_count"]["count"], 1)

    def test_projects_page_filters_by_partial_name(self):
        response = self.client.get(f"{self.projects_url}?name=cortex")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.active_project.name)
        self.assertNotContains(response, self.other_active_project.name)
        self.assertNotContains(response, self.inactive_project.name)
        self.assertEqual(response.context["record_count"]["count"], 1)

    def test_projects_page_filters_by_partial_lab_member(self):
        response = self.client.get(f"{self.projects_url}?lab_member=ali")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.active_project.name)
        self.assertNotContains(response, self.other_active_project.name)
        self.assertNotContains(response, self.inactive_project.name)
        self.assertEqual(response.context["record_count"]["count"], 1)

    def test_inactive_projects_page_filters_by_partial_lab_member(self):
        response = self.client.get(
            f"{self.projects_url}?status=inactive&lab_member=ali"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.inactive_project.name)
        self.assertNotContains(response, self.active_project.name)
        self.assertNotContains(response, self.other_active_project.name)
        self.assertEqual(response.context["record_count"]["count"], 1)

    def test_add_project_creates_project(self):
        response = self.client.post(
            self.add_project_url,
            {
                "return_url": self.projects_url,
                "name": "New Synapse Project",
                "lab_member": "CD",
                "description": "New project description",
                "start_date": "2026-04-01",
                "end_date": "2026-09-01",
                "active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.projects_url)

        project = Project.objects.get(name="New Synapse Project")
        self.assertEqual(project.lab_member, "CD")
        self.assertEqual(project.description, "New project description")
        self.assertEqual(project.start_date, date(2026, 4, 1))
        self.assertEqual(project.end_date, date(2026, 9, 1))
        self.assertTrue(project.active)

    def test_edit_project_updates_fields(self):
        response = self.client.post(
            reverse("lab:edit_project", args=[self.active_project.pk]),
            {
                "return_url": self.projects_url,
                "name": "Updated Cortex Mapping",
                "lab_member": "EF",
                "description": "Updated description",
                "start_date": "2026-05-01",
                "end_date": "2026-10-01",
                "active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.projects_url)

        self.active_project.refresh_from_db()
        self.assertEqual(self.active_project.name, "Updated Cortex Mapping")
        self.assertEqual(self.active_project.lab_member, "EF")
        self.assertEqual(self.active_project.description, "Updated description")
        self.assertEqual(self.active_project.start_date, date(2026, 5, 1))
        self.assertEqual(self.active_project.end_date, date(2026, 10, 1))
        self.assertTrue(self.active_project.active)

    def test_edit_project_can_mark_project_inactive(self):
        response = self.client.post(
            reverse("lab:edit_project", args=[self.active_project.pk]),
            {
                "return_url": self.projects_url,
                "name": self.active_project.name,
                "lab_member": self.active_project.lab_member,
                "description": self.active_project.description,
                "start_date": "2026-01-10",
                "end_date": "2026-07-30",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.active_project.refresh_from_db()
        self.assertFalse(self.active_project.active)

        response = self.client.get(self.projects_url)
        active_project_names = [
            str(row["cells"][0]["value"])
            for row in response.context["table"]["rows"]
        ]
        self.assertNotIn(self.active_project.name, active_project_names)

        response = self.client.get(f"{self.projects_url}?status=inactive")
        self.assertContains(response, self.active_project.name)


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class ExperimentsPageTests(AuthenticatedTestCase):
    def setUp(self):
        self.imaging_type = RecordingType.objects.get(slug="imaging")
        self.abr_type = RecordingType.objects.get(slug="abr")
        self.project = Project.objects.create(
            name="Auditory Project",
            lab_member="Alice",
            active=True,
        )
        self.other_project = Project.objects.create(
            name="Other Project",
            lab_member="Bob",
            active=True,
        )
        self.experiments_url = reverse(
            "lab:experiments_page",
            args=[self.project.pk],
        )
        self.add_experiment_url = reverse(
            "lab:add_experiment",
            args=[self.project.pk],
        )
        self.current_experiment = Experiment.objects.create(
            project=self.project,
            name="Tone Mapping",
            recording_type=self.imaging_type,
            description="Frequency response mapping",
            start_date=date(2026, 1, 10),
            end_date=date(2026, 7, 30),
            archived=False,
        )
        self.other_current_experiment = Experiment.objects.create(
            project=self.project,
            name="Noise Pilot",
            recording_type=self.imaging_type,
            description="Pilot noise responses",
            start_date=date(2026, 2, 1),
            archived=False,
        )
        self.archived_experiment = Experiment.objects.create(
            project=self.project,
            name="Archived ABR",
            recording_type=self.abr_type,
            description="Completed ABR analysis",
            start_date=date(2025, 3, 5),
            end_date=date(2025, 12, 20),
            archived=True,
        )
        self.other_project_experiment = Experiment.objects.create(
            project=self.other_project,
            name="Other Project Experiment",
            recording_type=self.imaging_type,
            archived=False,
        )

    def test_project_name_links_to_experiments_page(self):
        response = self.client.get(reverse("lab:projects_page"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.project.name)
        self.assertContains(response, f'href="{self.experiments_url}"')

    def test_experiments_page_defaults_to_current_project_experiments(self):
        response = self.client.get(self.experiments_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["project"], self.project)
        self.assertEqual(response.context["status"], "current")
        self.assertContains(response, self.current_experiment.name)
        self.assertContains(response, self.other_current_experiment.name)
        self.assertNotContains(response, self.archived_experiment.name)
        self.assertNotContains(response, self.other_project_experiment.name)
        self.assertEqual(response.context["record_count"]["count"], 2)

    def test_experiments_page_renders_columns_actions_and_back_buttons(self):
        response = self.client.get(self.experiments_url)

        self.assertEqual(response.status_code, 200)
        for label in (
            "Name",
            "Type",
            "Description",
            "Start Date",
            "End Date",
            "Actions",
        ):
            self.assertContains(response, label)

        self.assertContains(response, 'href="/"')
        self.assertContains(response, f'href="{reverse("lab:projects_page")}"')
        self.assertContains(response, "Add an experiment")
        self.assertContains(
            response,
            f'href="{self.add_experiment_url}?return_url=',
        )
        self.assertContains(response, "Edit")
        self.assertContains(
            response,
            reverse(
                "lab:edit_experiment",
                args=[self.project.pk, self.current_experiment.pk],
            ),
        )
        self.assertContains(
            response,
            reverse(
                "lab:recordings_page",
                args=[self.project.pk, self.current_experiment.pk],
            ),
        )

    def test_experiments_page_archived_tab_shows_archived_only(self):
        response = self.client.get(f"{self.experiments_url}?status=archived")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["status"], "archived")
        self.assertContains(response, self.archived_experiment.name)
        self.assertNotContains(response, self.current_experiment.name)
        self.assertNotContains(response, self.other_current_experiment.name)
        self.assertNotContains(response, self.other_project_experiment.name)
        self.assertEqual(response.context["record_count"]["count"], 1)

    def test_experiments_page_filters_by_partial_name(self):
        response = self.client.get(f"{self.experiments_url}?name=tone")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.current_experiment.name)
        self.assertNotContains(response, self.other_current_experiment.name)
        self.assertNotContains(response, self.archived_experiment.name)
        self.assertEqual(response.context["record_count"]["count"], 1)

    def test_add_experiment_creates_experiment_for_project(self):
        response = self.client.post(
            self.add_experiment_url,
            {
                "return_url": self.experiments_url,
                "name": "New Experiment",
                "recording_type_query": "imag",
                "description": "New experiment description",
                "start_date": "2026-04-01",
                "end_date": "2026-09-01",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.experiments_url)

        experiment = Experiment.objects.get(name="New Experiment")
        self.assertEqual(experiment.project, self.project)
        self.assertEqual(experiment.recording_type, self.imaging_type)
        self.assertEqual(experiment.description, "New experiment description")
        self.assertEqual(experiment.start_date, date(2026, 4, 1))
        self.assertEqual(experiment.end_date, date(2026, 9, 1))
        self.assertFalse(experiment.archived)

    def test_edit_experiment_updates_fields(self):
        response = self.client.post(
            reverse(
                "lab:edit_experiment",
                args=[self.project.pk, self.current_experiment.pk],
            ),
            {
                "return_url": self.experiments_url,
                "name": "Updated Tone Mapping",
                "recording_type_query": self.abr_type.name,
                "description": "Updated description",
                "start_date": "2026-05-01",
                "end_date": "2026-10-01",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.experiments_url)

        self.current_experiment.refresh_from_db()
        self.assertEqual(self.current_experiment.name, "Updated Tone Mapping")
        self.assertEqual(
            self.current_experiment.description,
            "Updated description",
        )
        self.assertEqual(self.current_experiment.recording_type, self.abr_type)
        self.assertEqual(self.current_experiment.start_date, date(2026, 5, 1))
        self.assertEqual(self.current_experiment.end_date, date(2026, 10, 1))
        self.assertFalse(self.current_experiment.archived)

    def test_edit_experiment_can_archive_experiment(self):
        response = self.client.post(
            reverse(
                "lab:edit_experiment",
                args=[self.project.pk, self.current_experiment.pk],
            ),
            {
                "return_url": self.experiments_url,
                "name": self.current_experiment.name,
                "recording_type_query": self.current_experiment.recording_type.name,
                "description": self.current_experiment.description,
                "start_date": "2026-01-10",
                "end_date": "2026-07-30",
                "archived": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.current_experiment.refresh_from_db()
        self.assertTrue(self.current_experiment.archived)

        response = self.client.get(self.experiments_url)
        current_experiment_names = [
            str(row["cells"][0]["value"])
            for row in response.context["table"]["rows"]
        ]
        self.assertNotIn(
            self.current_experiment.name,
            current_experiment_names,
        )

        response = self.client.get(f"{self.experiments_url}?status=archived")
        self.assertContains(response, self.current_experiment.name)

    def test_edit_experiment_is_project_scoped(self):
        response = self.client.get(
            reverse(
                "lab:edit_experiment",
                args=[self.other_project.pk, self.current_experiment.pk],
            )
        )

        self.assertEqual(response.status_code, 404)


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class ServicesPageTests(AuthenticatedTestCase):
    def setUp(self):
        self.services_url = reverse("lab:services_page")
        self.services_project = Project.objects.get(
            name="Services",
            is_service=True,
        )
        self.genotyping_type = RecordingType.objects.get(slug="genotyping")
        self.genotyping_experiment = Experiment.objects.get(
            project=self.services_project,
            name="Genotyping",
        )

    def test_services_page_renders_service_experiment_cards(self):
        response = self.client.get(self.services_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Genotyping")
        self.assertContains(
            response,
            reverse(
                "lab:recordings_page",
                args=[
                    self.services_project.pk,
                    self.genotyping_experiment.pk,
                ],
            ),
        )
        self.assertNotContains(response, "Add an experiment")
        self.assertNotContains(response, "experiments-table")
        self.assertNotContains(response, "Archived")

    def test_services_page_hides_archived_services(self):
        archived_experiment = Experiment.objects.create(
            project=self.services_project,
            name="Retired assay",
            recording_type=self.genotyping_type,
            archived=True,
        )

        response = self.client.get(self.services_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.genotyping_experiment.name)
        self.assertNotContains(response, archived_experiment.name)
        self.assertNotContains(response, "Archived")

        response = self.client.get(f"{self.services_url}?status=archived")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.genotyping_experiment.name)
        self.assertNotContains(response, archived_experiment.name)
        self.assertNotContains(response, "Archived")

    def test_services_page_repairs_builtin_services_project(self):
        Project.objects.filter(pk=self.services_project.pk).update(
            active=False,
            is_service=False,
            description="",
        )

        response = self.client.get(self.services_url)

        self.assertEqual(response.status_code, 200)
        self.services_project.refresh_from_db()
        self.assertTrue(self.services_project.active)
        self.assertTrue(self.services_project.is_service)
        self.assertTrue(self.services_project.description)
        self.assertContains(response, self.genotyping_experiment.name)

    def test_public_project_routes_hide_service_project(self):
        response = self.client.get(
            reverse("lab:experiments_page", args=[self.services_project.pk])
        )

        self.assertRedirects(response, self.services_url)

        response = self.client.get(
            reverse("lab:add_experiment", args=[self.services_project.pk])
        )

        self.assertEqual(response.status_code, 404)

        response = self.client.get(
            reverse(
                "lab:edit_experiment",
                args=[
                    self.services_project.pk,
                    self.genotyping_experiment.pk,
                ],
            )
        )

        self.assertEqual(response.status_code, 404)

        response = self.client.get(
            reverse("lab:edit_project", args=[self.services_project.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_service_recordings_page_links_back_to_services(self):
        response = self.client.get(
            reverse(
                "lab:recordings_page",
                args=[
                    self.services_project.pk,
                    self.genotyping_experiment.pk,
                ],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{self.services_url}"')
        self.assertContains(response, "Services")
        self.assertNotContains(response, "Experiment Page")


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class RecordingsPageTests(AuthenticatedTestCase):
    def setUp(self):
        self.imaging_type = RecordingType.objects.get(slug="imaging")
        self.protocol = Protocol.objects.create(
            protocol_number=1001,
            licence_reference="licence-1001",
            name="Protocol 1001",
        )
        self.mouse_1 = Mouse.objects.create(
            mouse_id="REC-PAGE-M1",
            protocol=self.protocol,
        )
        self.mouse_2 = Mouse.objects.create(
            mouse_id="REC-PAGE-M2",
            protocol=self.protocol,
        )
        self.project = Project.objects.create(
            name="Recording project",
        )
        self.experiment = Experiment.objects.create(
            project=self.project,
            name="Recording experiment",
            recording_type=self.imaging_type,
        )
        self.recordings_url = reverse(
            "lab:recordings_page",
            args=[self.project.pk, self.experiment.pk],
        )
        self.add_recording_url = reverse(
            "lab:add_recording",
            args=[self.project.pk, self.experiment.pk],
        )
        self.select_mouse_url = reverse(
            "lab:select_recording_mouse",
            args=[self.project.pk, self.experiment.pk],
        )
        self.duplicate_recording_url = reverse(
            "lab:duplicate_recording",
            args=[self.project.pk, self.experiment.pk],
        )
        self.objective_field = RecordingField.objects.get(
            recording_type=self.imaging_type,
            key="objective",
        )
        self.fps_field = RecordingField.objects.get(
            recording_type=self.imaging_type,
            key="fps",
        )
        self.frames_field = RecordingField.objects.get(
            recording_type=self.imaging_type,
            key="frames",
        )
        self.discarded_field = RecordingField.objects.get(
            recording_type=self.imaging_type,
            key="discarded",
        )
        self.newer_recording = create_recording(
            owner=self.user,
            mouse=self.mouse_1,
            experiment=self.experiment,
            recording_type=self.imaging_type,
            recording_date=date(2026, 7, 25),
            sequence_number=2,
            data_path="/data/imaging/newer",
            notes="responsive cell",
            values={
                "objective": "20x",
                "fps": 30.0,
                "frames": 600,
                "discarded": False,
            },
        )
        self.older_recording = create_recording(
            owner=self.user,
            mouse=self.mouse_2,
            experiment=self.experiment,
            recording_type=self.imaging_type,
            recording_date=date(2026, 7, 10),
            sequence_number=1,
            data_path="/data/imaging/older",
            notes="baseline run",
            values={
                "objective": "40x",
                "fps": 15.0,
                "frames": 300,
                "discarded": True,
            },
        )
        self.edit_newer_recording_url = reverse(
            "lab:edit_recording",
            args=[
                self.project.pk,
                self.experiment.pk,
                self.newer_recording.pk,
            ],
        )
        self.delete_newer_recording_url = reverse(
            "lab:delete_recording",
            args=[
                self.project.pk,
                self.experiment.pk,
                self.newer_recording.pk,
            ],
        )

    def assign_crossing_to_mouse(self, mouse, crossing):
        mouse.crossing_definition = crossing
        mouse.crossing = crossing.display_name
        mouse.crossing_type = crossing.inferred_crossing_type
        mouse.save(
            update_fields=[
                "crossing_definition",
                "crossing",
                "crossing_type",
            ],
        )

    def genotyping_recording_setup(self):
        genotyping_type = RecordingType.objects.get(slug="genotyping")
        genotyping_experiment = Experiment.objects.get(
            project__is_service=True,
            name="Genotyping",
            recording_type=genotyping_type,
        )
        crossing_field = RecordingField.objects.get(
            recording_type=genotyping_type,
            key="crossing",
        )
        add_url = reverse(
            "lab:add_recording",
            args=[
                genotyping_experiment.project_id,
                genotyping_experiment.pk,
            ],
        )

        return genotyping_experiment, crossing_field, add_url

    def test_experiment_name_links_to_recordings_page(self):
        response = self.client.get(
            reverse("lab:experiments_page", args=[self.project.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{self.recordings_url}"')

    def test_recordings_page_renders_sorted_table_and_actions(self):
        response = self.client.get(self.recordings_url)
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertLess(
            content.index(self.newer_recording.data_path),
            content.index(self.older_recording.data_path),
        )

        for label in (
            "Date",
            "Mice",
            "Sequence",
            "Objective",
            "FPS",
            "Frames",
            "Discarded",
            "Recording Path",
            "Notes",
        ):
            self.assertContains(response, label)

        self.assertNotContains(response, "Recording ID")
        self.assertContains(response, 'href="/"')
        self.assertContains(response, "Experiment Page")
        self.assertContains(response, "Add one recording")
        self.assertContains(response, "Duplicate selected recording")
        self.assertContains(response, 'name="selected_recording"')
        self.assertContains(response, self.add_recording_url)
        self.assertContains(response, self.duplicate_recording_url)
        self.assertContains(response, self.edit_newer_recording_url)
        self.assertContains(response, self.delete_newer_recording_url)
        self.assertContains(response, "Select all")
        self.assertContains(response, "data-table-select-all-button")
        self.assertContains(response, 'aria-label="Select all recordings"')
        self.assertContains(response, 'name="per_page"')
        self.assertEqual(response.context["page_size_control"]["current"], 25)
        for page_size in (25, 50, 100, 150, 200):
            self.assertContains(response, f'value="{page_size}"')

    def test_recordings_page_honours_page_size(self):
        for index in range(30):
            create_recording(
                owner=self.user,
                mouse=self.mouse_1,
                experiment=self.experiment,
                recording_type=self.imaging_type,
                recording_date=date(2026, 7, 11),
                sequence_number=index + 10,
                data_path=f"/data/imaging/page-size-{index}",
            )

        response = self.client.get(f"{self.recordings_url}?per_page=50")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["record_count"]["end"], 32)
        self.assertEqual(response.context["page_size_control"]["current"], 50)

    def test_recordings_page_filters(self):
        cases = [
            (
                "date_from=2026-07-20&date_to=2026-07-30",
                self.newer_recording,
                self.older_recording,
            ),
            (
                "mouse=PAGE-M2",
                self.older_recording,
                self.newer_recording,
            ),
            (
                "notes=responsive",
                self.newer_recording,
                self.older_recording,
            ),
        ]

        for query_string, expected, unexpected in cases:
            with self.subTest(query_string=query_string):
                response = self.client.get(f"{self.recordings_url}?{query_string}")

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, expected.data_path)
                self.assertNotContains(response, unexpected.data_path)
                self.assertEqual(response.context["record_count"]["count"], 1)

    def test_recordings_page_filters_filterable_text_recording_fields(self):
        self.objective_field.filterable = True
        self.objective_field.save(update_fields=["filterable"])

        response = self.client.get(
            self.recordings_url,
            {
                f"field_{self.objective_field.pk}": "20",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'name="field_{self.objective_field.pk}"')
        self.assertContains(response, self.newer_recording.data_path)
        self.assertNotContains(response, self.older_recording.data_path)
        self.assertEqual(response.context["record_count"]["count"], 1)

    def test_genotyping_crossing_filter_uses_crossing_autocomplete(self):
        genotyping_experiment, crossing_field, _ = self.genotyping_recording_setup()
        recordings_url = reverse(
            "lab:recordings_page",
            args=[genotyping_experiment.project_id, genotyping_experiment.pk],
        )

        response = self.client.get(recordings_url)
        crossing_filter = next(
            field
            for field in response.context["filter_form"]["fields"]
            if field["name"] == f"field_{crossing_field.pk}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["filter_form"]["has_autocomplete"])
        self.assertEqual(
            crossing_filter["autocomplete"],
            {
                "url": reverse("lab:crossing_lookup"),
                "value_key": "display_name",
                "label_key": "label",
            },
        )
        self.assertContains(response, f'name="field_{crossing_field.pk}"')
        self.assertContains(response, 'data-filter-autocomplete-input="true"')
        self.assertContains(response, 'data-autocomplete-value-key="display_name"')

    def test_recordings_page_filters_filterable_boolean_recording_fields(self):
        self.discarded_field.filterable = True
        self.discarded_field.save(update_fields=["filterable"])

        response = self.client.get(
            self.recordings_url,
            {
                f"field_{self.discarded_field.pk}": "true",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'name="field_{self.discarded_field.pk}"')
        self.assertContains(response, self.older_recording.data_path)
        self.assertNotContains(response, self.newer_recording.data_path)
        self.assertEqual(response.context["record_count"]["count"], 1)

    def test_recordings_page_invalid_numeric_recording_field_filter_matches_none(
        self,
    ):
        self.fps_field.filterable = True
        self.fps_field.save(update_fields=["filterable"])

        response = self.client.get(
            self.recordings_url,
            {
                f"field_{self.fps_field.pk}": "fast",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.newer_recording.data_path)
        self.assertNotContains(response, self.older_recording.data_path)
        self.assertEqual(response.context["record_count"]["count"], 0)

    def test_recordings_page_opens_page_containing_selected_recording(self):
        for index in range(25):
            create_recording(
                owner=self.user,
                mouse=self.mouse_1,
                experiment=self.experiment,
                recording_type=self.imaging_type,
                recording_date=date(2026, 7, 11),
                sequence_number=index + 10,
                data_path=f"/data/imaging/filler-{index}",
            )

        response = self.client.get(
            f"{self.recordings_url}?selected_recording={self.older_recording.pk}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["pagination"]["number"], 2)
        self.assertContains(response, self.older_recording.data_path)
        self.assertNotContains(response, self.newer_recording.data_path)
        self.assertContains(
            response,
            f'id="recording-{self.older_recording.pk}"',
        )
        self.assertNotContains(response, "table-row-selected")
        self.assertContains(response, "checked")

    def test_add_recording_form_renders_read_only_mouse_selector(self):
        response = self.client.get(self.add_recording_url)
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add mice")
        self.assertContains(response, self.select_mouse_url)
        self.assertContains(response, "No mice selected")
        self.assertContains(response, 'type="hidden" name="mice"')
        self.assertNotContains(response, reverse("lab:mouse_lookup"))
        self.assertNotContains(response, 'data-mouse-lookup-input="true"')
        self.assertNotContains(response, 'data-mouse-lookup-results')
        self.assertNotContains(response, "Mouse page")
        self.assertNotContains(response, "Add new mouse")
        self.assertNotContains(response, "return_with_mouse=1")
        self.assertNotContains(response, 'list="mouse-options"')
        self.assertLess(
            content.index('name="mice"'),
            content.index("Add mice"),
        )
        self.assertLess(
            content.index("Add mice"),
            content.index('name="recording_date"'),
        )

    def test_select_recording_mouse_page_honours_page_size(self):
        for index in range(30):
            Mouse.objects.create(
                mouse_id=f"REC-PAGE-SELECT-{index}",
                protocol=self.protocol,
            )

        response = self.client.get(f"{self.select_mouse_url}?per_page=50")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["record_count"]["end"], 32)
        self.assertEqual(response.context["page_size_control"]["current"], 50)
        self.assertContains(response, 'name="per_page"')
        for page_size in (25, 50, 100, 150, 200):
            self.assertContains(response, f'value="{page_size}"')

    def test_mouse_lookup_returns_partial_matches(self):
        response = self.client.get(
            reverse("lab:mouse_lookup"),
            {
                "q": "PAGE-M1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "results": [
                    {
                        "id": self.mouse_1.pk,
                        "label": self.mouse_1.mouse_id,
                    },
                ],
            },
        )

    def test_select_recording_mouse_page_filters_and_links_back(self):
        response = self.client.get(
            self.select_mouse_url,
            {
                "return_url": self.add_recording_url,
                "mouse_id": "PAGE-M2",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select mice")
        self.assertContains(response, "Back to recording form")
        self.assertContains(response, "Add mouse")
        self.assertContains(response, "Add selected mice")
        self.assertContains(response, reverse("lab:add_mouse_all"))
        self.assertContains(response, "return_with_mouse=1")
        self.assertNotContains(response, "Mouse page")
        self.assertNotContains(response, "Add new mouse")
        self.assertContains(response, 'name="selected_mouse"')
        self.assertContains(response, self.mouse_2.mouse_id)
        self.assertNotContains(response, self.mouse_1.mouse_id)
        self.assertNotContains(response, "Use mouse")

    def test_select_recording_mouse_page_adds_multiple_selected_mice(self):
        response = self.client.post(
            self.select_mouse_url,
            {
                "return_url": self.add_recording_url,
                "selected_mouse": [self.mouse_1.pk, self.mouse_2.pk],
            },
        )

        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response["Location"]).query)
        self.assertEqual(response["Location"].split("?")[0], self.add_recording_url)
        self.assertEqual(
            query["mice"],
            [f"{self.mouse_1.pk},{self.mouse_2.pk}"],
        )

    def test_add_recording_prefills_mouse_from_selection_query(self):
        response = self.client.get(
            self.add_recording_url,
            {
                "mice": f"{self.mouse_1.pk}\n{self.mouse_2.pk}",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'type="hidden" name="mice"')
        self.assertContains(
            response,
            self.mouse_1.pk,
        )
        self.assertContains(
            response,
            self.mouse_2.pk,
        )

    def test_genotyping_form_infers_crossing_from_selected_mice(self):
        _, crossing_field, add_url = self.genotyping_recording_setup()
        crossing, _ = create_test_crossing(("Genotyping Mut",))
        self.assign_crossing_to_mouse(self.mouse_1, crossing)
        self.assign_crossing_to_mouse(self.mouse_2, crossing)

        response = self.client.get(
            add_url,
            {
                "mice": f"{self.mouse_1.pk},{self.mouse_2.pk}",
            },
        )

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        crossing_field_name = form.dynamic_field_name(crossing_field)

        self.assertTrue(form.fields[crossing_field_name].disabled)
        self.assertEqual(
            form.fields[crossing_field_name].initial,
            crossing.display_name,
        )

    def test_genotyping_save_stores_inferred_crossing(self):
        _, crossing_field, add_url = self.genotyping_recording_setup()
        crossing, _ = create_test_crossing(("Genotyping Mut",))
        self.assign_crossing_to_mouse(self.mouse_1, crossing)
        self.assign_crossing_to_mouse(self.mouse_2, crossing)

        response = self.client.post(
            add_url,
            {
                "return_url": self.recordings_url,
                "mice": f"{self.mouse_1.pk}\n{self.mouse_2.pk}",
                "recording_date": "2026-08-02",
                "sequence_number": "1",
                "data_path": "/data/genotyping/inferred-crossing",
                f"value_{crossing_field.pk}": "Wrong crossing",
            },
        )

        self.assertEqual(response.status_code, 302)
        recording = Recording.objects.get(
            data_path="/data/genotyping/inferred-crossing"
        )
        self.assertEqual(recording.values["crossing"], crossing.display_name)
        self.assertEqual(
            list(
                recording.mice.order_by("mouse_id").values_list(
                    "mouse_id",
                    flat=True,
                )
            ),
            [self.mouse_1.pk, self.mouse_2.pk],
        )

    def test_genotyping_rejects_mice_from_different_crossings(self):
        _, crossing_field, add_url = self.genotyping_recording_setup()
        crossing, _ = create_test_crossing(("Genotyping Mut",))
        other_crossing, _ = create_test_crossing(("Other Mut",))
        self.assign_crossing_to_mouse(self.mouse_1, crossing)
        self.assign_crossing_to_mouse(self.mouse_2, other_crossing)

        response = self.client.post(
            add_url,
            {
                "return_url": self.recordings_url,
                "mice": f"{self.mouse_1.pk}\n{self.mouse_2.pk}",
                "recording_date": "2026-08-02",
                "sequence_number": "1",
                "data_path": "/data/genotyping/mixed-crossing",
                f"value_{crossing_field.pk}": crossing.display_name,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Genotyping records can only include mice from one crossing.",
            response.context["form"].errors["mice"][0],
        )
        self.assertFalse(
            Recording.objects.filter(
                data_path="/data/genotyping/mixed-crossing",
            ).exists()
        )

    def test_genotyping_rejects_mice_without_crossing(self):
        _, crossing_field, add_url = self.genotyping_recording_setup()

        response = self.client.post(
            add_url,
            {
                "return_url": self.recordings_url,
                "mice": self.mouse_1.pk,
                "recording_date": "2026-08-02",
                "sequence_number": "1",
                "data_path": "/data/genotyping/missing-crossing",
                f"value_{crossing_field.pk}": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Select mice with a crossing before creating a genotyping record",
            response.context["form"].errors["mice"][0],
        )
        self.assertFalse(
            Recording.objects.filter(
                data_path="/data/genotyping/missing-crossing",
            ).exists()
        )

    def test_add_mouse_from_selector_returns_to_recording_form_with_new_mouse(self):
        crossing, lines = create_test_crossing(("Recording mouse",))
        response = self.client.post(
            reverse("lab:add_mouse_all"),
            {
                "return_url": self.add_recording_url,
                "return_with_mouse": "1",
                "mouse_id": "REC-NEW-MOUSE",
                "date_of_birth": "",
                "tattoo": "",
                "genotype": "New recording mouse",
                "crossing": "",
                "crossing_type": "",
                "sex": "",
                "status": "Alive",
                "protocol_start_date": "",
                "cull_date": "",
                "custom_metadata": "{}",
                "protocol": str(self.protocol.pk),
                "severity": "",
                "notes": "",
                **crossing_post_data(crossing, lines),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            f"{self.add_recording_url}?mice=REC-NEW-MOUSE",
        )
        self.assertTrue(Mouse.objects.filter(pk="REC-NEW-MOUSE").exists())

    def test_add_recording_creates_recording_with_experiment_type(self):
        response = self.client.post(
            self.add_recording_url,
            {
                "return_url": self.recordings_url,
                "mice": "REC-PAGE-M1",
                "recording_date": "2026-08-01",
                "sequence_number": "3",
                "data_path": "/data/imaging/added",
                "notes": "added note",
                f"value_{self.objective_field.pk}": "60x",
                f"value_{self.fps_field.pk}": "45.5",
                f"value_{self.frames_field.pk}": "1200",
                f"value_{self.discarded_field.pk}": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.recordings_url)

        recording = Recording.objects.get(data_path="/data/imaging/added")
        self.assertEqual(recording.owner, self.user)
        self.assertEqual(recording.experiment, self.experiment)
        self.assertEqual(recording.recording_type, self.imaging_type)
        self.assertEqual(list(recording.mice.all()), [self.mouse_1])
        self.assertEqual(recording.recording_date, date(2026, 8, 1))
        self.assertEqual(recording.sequence_number, 3)
        self.assertEqual(recording.notes, "added note")
        self.assertEqual(recording.values["objective"], "60x")
        self.assertEqual(recording.values["fps"], 45.5)
        self.assertEqual(recording.values["frames"], 1200)
        self.assertTrue(recording.values["discarded"])

    def test_add_recording_can_attach_multiple_mice(self):
        response = self.client.post(
            self.add_recording_url,
            {
                "return_url": self.recordings_url,
                "mice": f"{self.mouse_1.pk}\n{self.mouse_2.pk}",
                "recording_date": "2026-08-01",
                "sequence_number": "3",
                "data_path": "/data/genotyping/group-gel",
                "notes": "group gel",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.recordings_url)

        recording = Recording.objects.get(data_path="/data/genotyping/group-gel")
        self.assertEqual(
            list(
                recording.mice.order_by("mouse_id").values_list(
                    "mouse_id",
                    flat=True,
                )
            ),
            [self.mouse_1.pk, self.mouse_2.pk],
        )

        response = self.client.get(self.recordings_url)
        self.assertContains(
            response,
            reverse("lab:mouse_record", args=[self.mouse_1.pk]),
        )
        self.assertContains(
            response,
            reverse("lab:mouse_record", args=[self.mouse_2.pk]),
        )
        self.assertContains(response, f">{self.mouse_1.pk}</a><br>")
        self.assertContains(response, f">{self.mouse_2.pk}</a><br>")

    def test_add_recording_rejects_ambiguous_mouse_partial_match(self):
        response = self.client.post(
            self.add_recording_url,
            {
                "return_url": self.recordings_url,
                "mice": "REC-PAGE",
                "recording_date": "2026-08-01",
                "sequence_number": "3",
                "data_path": "/data/imaging/ambiguous-mouse",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Select registered mouse IDs: REC-PAGE.",
        )
        self.assertFalse(
            Recording.objects.filter(
                data_path="/data/imaging/ambiguous-mouse",
            ).exists()
        )

    def test_duplicate_selected_recording_prefills_add_form(self):
        response = self.client.post(
            self.duplicate_recording_url,
            {
                "return_url": self.recordings_url,
                "selected_recording": [self.newer_recording.pk],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(self.add_recording_url, response["Location"])
        self.assertIn(f"duplicate={self.newer_recording.pk}", response["Location"])

        response = self.client.get(response["Location"])

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/data/imaging/newer")
        self.assertContains(response, "responsive cell")
        self.assertContains(response, "20x")
        self.assertContains(response, "30.0")
        self.assertContains(response, "600")

    def test_edit_recording_requires_owner_or_staff(self):
        response = self.client.post(
            self.edit_newer_recording_url,
            {
                "return_url": self.recordings_url,
                "mice": self.mouse_2.pk,
                "recording_date": "2026-08-02",
                "sequence_number": "4",
                "data_path": "/data/imaging/edited",
                "notes": "edited note",
                f"value_{self.objective_field.pk}": "60x",
                f"value_{self.fps_field.pk}": "50.0",
                f"value_{self.frames_field.pk}": "900",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.recordings_url)

        self.newer_recording.refresh_from_db()
        self.assertEqual(self.newer_recording.owner, self.user)
        self.assertEqual(list(self.newer_recording.mice.all()), [self.mouse_2])
        self.assertEqual(self.newer_recording.data_path, "/data/imaging/edited")
        self.assertEqual(self.newer_recording.values["objective"], "60x")

        User = get_user_model()
        other_user = User.objects.create_user(
            username="other-recording-user",
            password="password",
        )
        self.client.force_login(other_user)

        response = self.client.get(self.edit_newer_recording_url)

        self.assertEqual(response.status_code, 403)

        staff_user = User.objects.create_user(
            username="recording-staff",
            password="password",
            is_staff=True,
        )
        self.client.force_login(staff_user)

        response = self.client.get(self.edit_newer_recording_url)

        self.assertEqual(response.status_code, 200)

    def test_delete_recording_requires_owner_or_staff(self):
        User = get_user_model()
        other_user = User.objects.create_user(
            username="delete-other-user",
            password="password",
        )
        self.client.force_login(other_user)

        response = self.client.post(
            self.delete_newer_recording_url,
            {
                "return_url": self.recordings_url,
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            Recording.objects.filter(pk=self.newer_recording.pk).exists()
        )

        self.client.force_login(self.user)
        response = self.client.post(
            self.delete_newer_recording_url,
            {
                "return_url": self.recordings_url,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.recordings_url)
        self.assertFalse(
            Recording.objects.filter(pk=self.newer_recording.pk).exists()
        )

    def test_other_user_cannot_duplicate_recording(self):
        User = get_user_model()
        other_user = User.objects.create_user(
            username="duplicate-other-user",
            password="password",
        )
        self.client.force_login(other_user)

        response = self.client.post(
            self.duplicate_recording_url,
            {
                "return_url": self.recordings_url,
                "selected_recording": [self.newer_recording.pk],
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_duplicate_selected_requires_exactly_one_recording(self):
        response = self.client.post(
            self.duplicate_recording_url,
            {
                "return_url": self.recordings_url,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.recordings_url)

        response = self.client.post(
            self.duplicate_recording_url,
            {
                "return_url": self.recordings_url,
                "selected_recording": [
                    self.newer_recording.pk,
                    self.older_recording.pk,
                ],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.recordings_url)


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
        self.assertNotContains(response, f'href="{reverse("lab:mice_page")}"')

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


class RecordingDataMigrationTests(TransactionTestCase):
    migrate_from = [("lab", "0025_dynamic_recording_schema")]
    migrate_to = [("lab", "0029_recording_owner")]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        self.old_apps = self.executor.loader.project_state(
            self.migrate_from
        ).apps

    def tearDown(self):
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        super().tearDown()

    def test_legacy_recordings_migrate_to_generic_recordings(self):
        Protocol = self.old_apps.get_model("lab", "Protocol")
        Mouse = self.old_apps.get_model("lab", "Mouse")
        Project = self.old_apps.get_model("lab", "Project")
        Experiment = self.old_apps.get_model("lab", "Experiment")
        ImagingRecording = self.old_apps.get_model("lab", "ImagingRecording")
        ABRRecording = self.old_apps.get_model("lab", "ABRRecording")

        protocol = Protocol.objects.create(
            protocol_number=901,
            licence_reference="licence-901",
            name="Protocol 901",
        )
        mouse = Mouse.objects.create(
            mouse_id="MIG-M1",
            protocol=protocol,
        )
        project = Project.objects.create(name="Migration project")
        experiment = Experiment.objects.create(
            project=project,
            name="Migration experiment",
        )
        ImagingRecording.objects.create(
            mouse=mouse,
            experiment=experiment,
            recording_date=date(2026, 7, 20),
            data_path="/data/imaging/migration",
            sequence_number=2,
            objective="20x",
            fps=30.0,
            frames=600,
            discarded=True,
        )
        ABRRecording.objects.create(
            mouse=mouse,
            experiment=experiment,
            recording_date=date(2026, 7, 21),
            data_path="/data/abr/migration",
            sequence_number=3,
            click_threshold_db=40,
            threshold_12khz_db=25,
        )

        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        new_apps = self.executor.loader.project_state(self.migrate_to).apps
        Recording = new_apps.get_model("lab", "Recording")
        RecordingType = new_apps.get_model("lab", "RecordingType")

        self.assertEqual(Recording.objects.count(), 2)
        self.assertEqual(
            set(RecordingType.objects.values_list("slug", flat=True)),
            {"abr", "imaging"},
        )

        imaging_recording = Recording.objects.get(
            recording_type__slug="imaging"
        )
        abr_recording = Recording.objects.get(recording_type__slug="abr")

        self.assertEqual(imaging_recording.mouse_id, mouse.pk)
        self.assertEqual(imaging_recording.experiment_id, experiment.pk)
        self.assertEqual(imaging_recording.sequence_number, 2)
        self.assertEqual(imaging_recording.data_path, "/data/imaging/migration")
        self.assertEqual(imaging_recording.values["objective"], "20x")
        self.assertEqual(imaging_recording.values["fps"], 30.0)
        self.assertEqual(imaging_recording.values["frames"], 600)
        self.assertTrue(imaging_recording.values["discarded"])

        self.assertEqual(abr_recording.sequence_number, 3)
        self.assertEqual(abr_recording.data_path, "/data/abr/migration")
        self.assertEqual(abr_recording.values["click_threshold_db"], 40)
        self.assertEqual(abr_recording.values["threshold_12khz_db"], 25)


class RecordingMiceMigrationTests(TransactionTestCase):
    migrate_from = [("lab", "0035_protocol_unique_number_per_licence")]
    migrate_to = [("lab", "0036_recording_mice")]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        self.old_apps = self.executor.loader.project_state(
            self.migrate_from
        ).apps

    def tearDown(self):
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        super().tearDown()

    def test_recording_mouse_migrates_to_mice_relationship(self):
        Protocol = self.old_apps.get_model("lab", "Protocol")
        Mouse = self.old_apps.get_model("lab", "Mouse")
        Project = self.old_apps.get_model("lab", "Project")
        Experiment = self.old_apps.get_model("lab", "Experiment")
        Recording = self.old_apps.get_model("lab", "Recording")
        RecordingType = self.old_apps.get_model("lab", "RecordingType")

        protocol = Protocol.objects.create(
            protocol_number=902,
            licence_reference="licence-902",
            name="Protocol 902",
        )
        mouse = Mouse.objects.create(
            mouse_id="MIG-M2M-M1",
            protocol=protocol,
        )
        project = Project.objects.create(name="M2M migration project")
        recording_type = RecordingType.objects.create(
            name="Genotyping",
            slug="genotyping",
        )
        experiment = Experiment.objects.create(
            project=project,
            name="M2M migration experiment",
            recording_type=recording_type,
        )
        recording = Recording.objects.create(
            mouse=mouse,
            experiment=experiment,
            recording_type=recording_type,
            recording_date=date(2026, 8, 20),
            data_path="/data/genotyping/migration",
        )

        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        new_apps = self.executor.loader.project_state(self.migrate_to).apps
        Recording = new_apps.get_model("lab", "Recording")

        migrated_recording = Recording.objects.get(pk=recording.pk)
        self.assertEqual(
            list(
                migrated_recording.mice.order_by("mouse_id").values_list(
                    "mouse_id",
                    flat=True,
                )
            ),
            [mouse.pk],
        )

        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_from)
        old_apps = self.executor.loader.project_state(self.migrate_from).apps
        Recording = old_apps.get_model("lab", "Recording")
        restored_recording = Recording.objects.get(pk=recording.pk)

        self.assertEqual(restored_recording.mouse_id, mouse.pk)


class ServicesSetupMigrationTests(TransactionTestCase):
    migrate_from = [("lab", "0036_recording_mice")]
    migrate_to = [("lab", "0037_services_project_and_genotyping")]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        self.old_apps = self.executor.loader.project_state(
            self.migrate_from
        ).apps

    def tearDown(self):
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        super().tearDown()

    def test_services_project_and_genotyping_schema_are_seeded(self):
        Experiment = self.old_apps.get_model("lab", "Experiment")
        Project = self.old_apps.get_model("lab", "Project")
        RecordingField = self.old_apps.get_model("lab", "RecordingField")
        RecordingType = self.old_apps.get_model("lab", "RecordingType")

        Experiment.objects.filter(
            project__name="Services",
            name="Genotyping",
        ).delete()
        RecordingField.objects.filter(
            recording_type__slug="genotyping",
        ).delete()
        RecordingType.objects.filter(slug="genotyping").delete()
        Project.objects.filter(name="Services").delete()

        existing_services_project = Project.objects.create(
            name="Services",
            active=False,
        )

        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        new_apps = self.executor.loader.project_state(self.migrate_to).apps
        Experiment = new_apps.get_model("lab", "Experiment")
        Project = new_apps.get_model("lab", "Project")
        RecordingField = new_apps.get_model("lab", "RecordingField")
        RecordingType = new_apps.get_model("lab", "RecordingType")

        services_project = Project.objects.get(pk=existing_services_project.pk)
        self.assertTrue(services_project.is_service)
        self.assertTrue(services_project.active)
        self.assertTrue(services_project.description)

        genotyping_type = RecordingType.objects.get(slug="genotyping")
        self.assertEqual(genotyping_type.name, "Genotyping")
        self.assertEqual(
            genotyping_type.plural_name,
            "Genotyping records",
        )

        genotyping_experiment = Experiment.objects.get(
            project=services_project,
            name="Genotyping",
        )
        self.assertEqual(
            genotyping_experiment.recording_type_id,
            genotyping_type.pk,
        )
        self.assertFalse(genotyping_experiment.archived)

        fields = {
            field.key: field
            for field in RecordingField.objects.filter(
                recording_type=genotyping_type,
            )
        }
        self.assertEqual(set(fields), {"lab_member", "crossing"})
        self.assertTrue(fields["lab_member"].display_in_table)
        self.assertFalse(fields["lab_member"].filterable)
        self.assertTrue(fields["crossing"].display_in_table)
        self.assertTrue(fields["crossing"].filterable)


class StructuredCrossingsMigrationTests(TransactionTestCase):
    migrate_from = [("lab", "0037_services_project_and_genotyping")]
    migrate_to = [("lab", "0038_structured_crossings")]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        self.old_apps = self.executor.loader.project_state(
            self.migrate_from
        ).apps

    def tearDown(self):
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        super().tearDown()

    def test_structured_crossings_seed_species_without_legacy_crossings(self):
        Mouse = self.old_apps.get_model("lab", "Mouse")
        Protocol = self.old_apps.get_model("lab", "Protocol")

        protocol = Protocol.objects.create(
            protocol_number=904,
            licence_reference="licence-904",
            name="Protocol 904",
        )
        Mouse.objects.create(
            mouse_id="STRUCT-MIG-M1",
            protocol=protocol,
            crossing="Legacy line",
            crossing_type="Single",
            genotype="Legacy genotype",
        )

        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        new_apps = self.executor.loader.project_state(self.migrate_to).apps
        Crossing = new_apps.get_model("lab", "Crossing")
        Mouse = new_apps.get_model("lab", "Mouse")
        Species = new_apps.get_model("lab", "Species")

        default_species = Species.objects.get(name="Mouse")
        self.assertTrue(default_species.active)
        self.assertEqual(Crossing.objects.count(), 0)

        mouse = Mouse.objects.get(pk="STRUCT-MIG-M1")
        self.assertIsNone(mouse.crossing_definition_id)
        self.assertEqual(mouse.crossing, "Legacy line")
        self.assertEqual(mouse.crossing_type, "Single")
        self.assertEqual(mouse.genotype, "Legacy genotype")


class RenameMultipleCrossingTypeMigrationTests(TransactionTestCase):
    migrate_from = [("lab", "0038_structured_crossings")]
    migrate_to = [("lab", "0039_rename_multiple_crossing_type")]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        self.old_apps = self.executor.loader.project_state(
            self.migrate_from
        ).apps

    def tearDown(self):
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        super().tearDown()

    def test_double_triple_crossing_type_migrates_to_multiple(self):
        Mouse = self.old_apps.get_model("lab", "Mouse")
        Protocol = self.old_apps.get_model("lab", "Protocol")

        protocol = Protocol.objects.create(
            protocol_number=905,
            licence_reference="licence-905",
            name="Protocol 905",
        )
        Mouse.objects.create(
            mouse_id="MULTIPLE-MIG-M1",
            protocol=protocol,
            crossing_type="Double_triple",
        )

        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        new_apps = self.executor.loader.project_state(self.migrate_to).apps
        Mouse = new_apps.get_model("lab", "Mouse")

        mouse = Mouse.objects.get(pk="MULTIPLE-MIG-M1")
        self.assertEqual(mouse.crossing_type, "Multiple")


class SingleLineCrossingBackfillMigrationTests(TransactionTestCase):
    migrate_from = [("lab", "0039_rename_multiple_crossing_type")]
    migrate_to = [("lab", "0040_backfill_single_line_crossings")]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        self.old_apps = self.executor.loader.project_state(
            self.migrate_from
        ).apps

    def tearDown(self):
        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        super().tearDown()

    def test_existing_lines_get_single_line_crossings(self):
        Crossing = self.old_apps.get_model("lab", "Crossing")
        MouseLine = self.old_apps.get_model("lab", "MouseLine")
        Species = self.old_apps.get_model("lab", "Species")

        species = Species.objects.get(name="Mouse")
        line = MouseLine.objects.create(
            species=species,
            name="Backfilled line",
        )
        self.assertFalse(
            Crossing.objects.filter(canonical_key=str(line.pk)).exists()
        )

        self.executor.loader.build_graph()
        self.executor.migrate(self.migrate_to)
        new_apps = self.executor.loader.project_state(self.migrate_to).apps
        Crossing = new_apps.get_model("lab", "Crossing")

        crossing = Crossing.objects.get(
            species_id=species.pk,
            canonical_key=str(line.pk),
        )
        self.assertEqual(crossing.display_name, "Backfilled line")
        self.assertEqual(
            list(crossing.lines.values_list("pk", flat=True)),
            [line.pk],
        )


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class RecordingModelTests(AuthenticatedTestCase):
    def setUp(self):
        self.protocol = Protocol.objects.create(
            protocol_number=401,
            licence_reference="licence-401",
            name="Protocol 401",
        )
        self.mouse = Mouse.objects.create(
            mouse_id="REC-M1",
            protocol=self.protocol,
        )
        self.project = Project.objects.create(
            name="Auditory project",
        )
        self.imaging_type = RecordingType.objects.get(slug="imaging")
        self.abr_type = RecordingType.objects.get(slug="abr")
        self.experiment = Experiment.objects.create(
            project=self.project,
            name="Experiment A",
            recording_type=self.imaging_type,
        )
        self.abr_experiment = Experiment.objects.create(
            project=self.project,
            name="Experiment B",
            recording_type=self.abr_type,
        )

    def test_generic_recordings_store_base_and_custom_fields(self):
        imaging_recording = create_recording(
            mouse=self.mouse,
            experiment=self.experiment,
            recording_type=self.imaging_type,
            recording_date=date(2026, 7, 20),
            data_path="/data/imaging/session-1",
            sequence_number=2,
            values={
                "objective": "20x",
                "fps": 30.0,
                "frames": 600,
                "discarded": False,
            },
        )
        abr_recording = create_recording(
            mouse=self.mouse,
            experiment=self.abr_experiment,
            recording_type=self.abr_type,
            recording_date=date(2026, 7, 21),
            data_path="/data/abr/session-1",
            sequence_number=3,
            values={
                "click_threshold_db": 35,
                "threshold_12khz_db": 20,
            },
        )

        self.assertEqual(imaging_recording.pk, imaging_recording.recording_id)
        self.assertEqual(abr_recording.pk, abr_recording.recording_id)
        self.assertEqual(imaging_recording.data_path, "/data/imaging/session-1")
        self.assertEqual(abr_recording.data_path, "/data/abr/session-1")
        self.assertEqual(imaging_recording.values["objective"], "20x")
        self.assertEqual(abr_recording.values["click_threshold_db"], 35)

        response = self.client.get(
            reverse("lab:mouse_record", args=[self.mouse.pk]),
        )
        project_url = reverse("lab:experiments_page", args=[self.project.pk])
        experiment_url = reverse(
            "lab:recordings_page",
            args=[self.project.pk, self.experiment.pk],
        )
        selected_recording_url = (
            f"{experiment_url}?selected_recording={imaging_recording.pk}"
            f"#recording-{imaging_recording.pk}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Imaging recordings")
        self.assertContains(response, "ABR recordings")
        self.assertContains(response, "/data/imaging/session-1")
        self.assertContains(response, "/data/abr/session-1")
        self.assertContains(response, "20x")
        self.assertContains(response, "30.0")
        self.assertContains(response, "600")
        self.assertContains(response, "35")
        self.assertContains(response, "20")
        self.assertContains(response, f'href="{project_url}"')
        self.assertContains(response, f'href="{experiment_url}"')
        self.assertContains(response, f'href="{selected_recording_url}"')

    def test_recording_string_handles_missing_mouse(self):
        recording = create_recording(
            experiment=self.experiment,
            recording_type=self.imaging_type,
            recording_date=date(2026, 7, 20),
            data_path="/data/imaging/orphaned-session",
            sequence_number=1,
        )

        self.assertIn("No mice", str(recording))

    def test_recording_can_be_modified_by_owner_or_staff(self):
        User = get_user_model()
        owner = User.objects.create_user(
            username="recording-owner",
            password="password",
        )
        other_user = User.objects.create_user(
            username="recording-other",
            password="password",
        )
        staff_user = User.objects.create_user(
            username="recording-admin",
            password="password",
            is_staff=True,
        )
        recording = create_recording(
            owner=owner,
            mouse=self.mouse,
            experiment=self.experiment,
            recording_type=self.imaging_type,
            recording_date=date(2026, 7, 20),
            data_path="/data/imaging/owned-session",
            sequence_number=1,
        )

        self.assertTrue(recording.can_be_modified_by(owner))
        self.assertTrue(recording.can_be_modified_by(staff_user))
        self.assertFalse(recording.can_be_modified_by(other_user))

    def test_recording_values_are_validated_against_type_fields(self):
        valid_recording = Recording(
            experiment=self.experiment,
            recording_type=self.imaging_type,
            recording_date=date(2026, 7, 20),
            data_path="/data/imaging/valid",
            values={
                "objective": "20x",
                "fps": 30.0,
                "frames": 600,
                "discarded": False,
            },
        )

        valid_recording.full_clean()

        invalid_recording = Recording(
            experiment=self.experiment,
            recording_type=self.imaging_type,
            recording_date=date(2026, 7, 20),
            data_path="/data/imaging/invalid",
            values={
                "fps": "fast",
            },
        )

        with self.assertRaises(ValidationError):
            invalid_recording.full_clean()

    def test_recording_values_reject_unknown_fields(self):
        recording = Recording(
            experiment=self.experiment,
            recording_type=self.imaging_type,
            recording_date=date(2026, 7, 20),
            data_path="/data/imaging/unknown",
            values={
                "unknown_field": "value",
            },
        )

        with self.assertRaises(ValidationError):
            recording.full_clean()

    def test_recording_type_must_match_experiment_type(self):
        recording = Recording(
            experiment=self.experiment,
            recording_type=self.abr_type,
            recording_date=date(2026, 7, 20),
            data_path="/data/abr/wrong-experiment",
        )

        with self.assertRaises(ValidationError):
            recording.full_clean()

    def test_recording_field_key_cannot_change_when_values_exist(self):
        create_recording(
            mouse=self.mouse,
            experiment=self.experiment,
            recording_type=self.imaging_type,
            recording_date=date(2026, 7, 20),
            data_path="/data/imaging/key-lock",
            values={
                "fps": 30.0,
            },
        )
        field = RecordingField.objects.get(
            recording_type=self.imaging_type,
            key="fps",
        )
        field.key = "hz"

        with self.assertRaises(ValidationError):
            field.full_clean()


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class RecordingAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin_user = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(self.admin_user)
        self.project = Project.objects.create(name="Admin project")
        self.recording_type = RecordingType.objects.get(slug="imaging")
        self.experiment = Experiment.objects.create(
            project=self.project,
            name="Admin experiment",
            recording_type=self.recording_type,
        )
        self.recording = Recording.objects.create(
            experiment=self.experiment,
            recording_type=self.recording_type,
            recording_date=date(2026, 7, 20),
            data_path="/data/admin/recording",
        )

    def test_admin_can_open_recording_type_add_page_with_field_inline(self):
        response = self.client.get(reverse("admin:lab_recordingtype_add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recording fields")
        self.assertContains(response, 'name="fields-0-key"')
        self.assertContains(response, 'name="fields-0-data_type"')

    def test_recording_admin_is_view_only(self):
        response = self.client.get(reverse("admin:lab_recording_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/data/admin/recording")

        response = self.client.get(reverse("admin:lab_recording_add"))
        self.assertEqual(response.status_code, 403)

        response = self.client.post(
            reverse(
                "admin:lab_recording_change",
                args=[self.recording.pk],
            ),
            {
                "data_path": "/data/admin/changed",
            },
        )
        self.assertEqual(response.status_code, 403)


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class MouseIdentityAndDeletionTests(AuthenticatedTestCase):
    def setUp(self):
        self.protocol = Protocol.objects.create(
            protocol_number=501,
            licence_reference="licence-501",
            name="Protocol 501",
        )
        self.other_protocol = Protocol.objects.create(
            protocol_number=502,
            licence_reference="licence-502",
            name="Protocol 502",
        )
        self.procedure_type = ProcedureType.objects.create(
            name="Surgery",
        )
        self.mouse = Mouse.objects.create(
            mouse_id="MOUSE-ID-1",
            protocol=self.protocol,
            genotype="Gad2",
            notes="Original notes",
        )
        self.second_mouse = Mouse.objects.create(
            mouse_id="MOUSE-ID-2",
            protocol=self.protocol,
        )
        self.other_mouse = Mouse.objects.create(
            mouse_id="MOUSE-ID-3",
            protocol=self.other_protocol,
        )
        self.project = Project.objects.create(
            name="Auditory project",
        )
        self.imaging_type = RecordingType.objects.get(slug="imaging")
        self.abr_type = RecordingType.objects.get(slug="abr")
        self.experiment = Experiment.objects.create(
            project=self.project,
            name="Experiment A",
            recording_type=self.imaging_type,
        )
        self.abr_experiment = Experiment.objects.create(
            project=self.project,
            name="Experiment B",
            recording_type=self.abr_type,
        )
        self.return_url = reverse(
            "lab:procedure_page_licence_protocol",
            kwargs={
                "licence_reference": self.protocol.licence_reference,
                "protocol_number": self.protocol.protocol_number,
            },
        )
        self.delete_selected_url = reverse(
            "lab:delete_selected_mice_licence_protocol",
            kwargs={
                "licence_reference": self.protocol.licence_reference,
                "protocol_number": self.protocol.protocol_number,
            },
        )

    def create_recordings(self, mouse):
        imaging_recording = create_recording(
            mouse=mouse,
            experiment=self.experiment,
            recording_type=self.imaging_type,
            recording_date=date(2026, 7, 20),
            data_path="/data/imaging/session-1",
            values={
                "objective": "20x",
                "fps": 30.0,
            },
        )
        abr_recording = create_recording(
            mouse=mouse,
            experiment=self.abr_experiment,
            recording_type=self.abr_type,
            recording_date=date(2026, 7, 21),
            data_path="/data/abr/session-1",
            values={
                "click_threshold_db": 35,
            },
        )

        return imaging_recording, abr_recording

    def test_mouse_id_remains_primary_key(self):
        self.assertEqual(Mouse._meta.pk.name, "mouse_id")

    def test_single_mouse_edit_renders_rename_header_and_delete_form_action(self):
        response = self.client.get(
            reverse("lab:edit_mouse", args=[self.mouse.pk]),
        )
        content = response.content.decode()
        rename_url = reverse("lab:rename_mouse", args=[self.mouse.pk])
        delete_url = reverse("lab:delete_mouse", args=[self.mouse.pk])
        form_start = content.index('<form method="post" class="form-card">')
        form_end = content.index("</form>", form_start)

        self.assertEqual(response.status_code, 200)
        self.assertIn(rename_url, content[:form_start])
        self.assertIn(delete_url, content[form_start:form_end])
        self.assertNotIn(delete_url, content[:form_start])
        self.assertContains(response, "Rename")
        self.assertContains(response, "Delete")

    def test_mouse_action_urls_accept_ids_containing_slashes(self):
        imported_mouse = Mouse.objects.create(
            mouse_id="1_M_LE / 411859",
            protocol=self.protocol,
        )
        procedure = Procedure.objects.create(
            mouse=imported_mouse,
            procedure_type=self.procedure_type,
            date=date(2026, 7, 20),
        )

        urls = [
            reverse("lab:mouse_record", args=[imported_mouse.pk]),
            reverse("lab:edit_mouse", args=[imported_mouse.pk]),
            reverse("lab:rename_mouse", args=[imported_mouse.pk]),
            reverse("lab:delete_mouse", args=[imported_mouse.pk]),
            reverse(
                "lab:edit_mouse_procedure",
                args=[imported_mouse.pk, procedure.pk],
            ),
        ]

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, imported_mouse.mouse_id)

    def test_rename_mouse_moves_related_records_to_new_primary_key(self):
        procedure = Procedure.objects.create(
            mouse=self.mouse,
            procedure_type=self.procedure_type,
            date=date(2026, 7, 20),
        )
        imaging_recording, abr_recording = self.create_recordings(self.mouse)

        response = self.client.post(
            reverse("lab:rename_mouse", args=[self.mouse.pk]),
            {
                "return_url": self.return_url,
                "new_mouse_id": "MOUSE-ID-RENAMED",
                "confirm_new_mouse_id": "MOUSE-ID-RENAMED",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response["Location"].startswith(
                reverse("lab:edit_mouse", args=["MOUSE-ID-RENAMED"])
            )
        )
        self.assertFalse(Mouse.objects.filter(pk="MOUSE-ID-1").exists())

        renamed_mouse = Mouse.objects.get(pk="MOUSE-ID-RENAMED")
        self.assertEqual(renamed_mouse.protocol, self.protocol)
        self.assertEqual(renamed_mouse.genotype, "Gad2")
        self.assertEqual(renamed_mouse.notes, "Original notes")

        procedure.refresh_from_db()
        imaging_recording.refresh_from_db()
        abr_recording.refresh_from_db()
        self.assertEqual(procedure.mouse, renamed_mouse)
        self.assertEqual(list(imaging_recording.mice.all()), [renamed_mouse])
        self.assertEqual(list(abr_recording.mice.all()), [renamed_mouse])

    def test_rename_mouse_rejects_invalid_ids(self):
        cases = [
            ("", "", "required"),
            ("MOUSE-ID-1", "MOUSE-ID-1", "same"),
            ("MOUSE-ID-2", "MOUSE-ID-2", "duplicate"),
            ("NEW-MOUSE-ID", "OTHER-MOUSE-ID", "mismatch"),
            ("X" * 101, "X" * 101, "too long"),
        ]

        for new_mouse_id, confirmation, label in cases:
            with self.subTest(label=label):
                response = self.client.post(
                    reverse("lab:rename_mouse", args=[self.mouse.pk]),
                    {
                        "return_url": self.return_url,
                        "new_mouse_id": new_mouse_id,
                        "confirm_new_mouse_id": confirmation,
                    },
                )

                self.assertEqual(response.status_code, 200)
                self.assertTrue(
                    Mouse.objects.filter(pk=self.mouse.pk).exists()
                )

        self.assertFalse(Mouse.objects.filter(pk="NEW-MOUSE-ID").exists())

    def test_delete_mouse_removes_mouse_and_procedures_but_keeps_recordings(self):
        procedure = Procedure.objects.create(
            mouse=self.mouse,
            procedure_type=self.procedure_type,
            date=date(2026, 7, 20),
        )
        imaging_recording, abr_recording = self.create_recordings(self.mouse)

        response = self.client.post(
            reverse("lab:delete_mouse", args=[self.mouse.pk]),
            {
                "return_url": self.return_url,
                "confirm_mouse_id": self.mouse.pk,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.return_url)
        self.assertFalse(Mouse.objects.filter(pk=self.mouse.pk).exists())
        self.assertFalse(Procedure.objects.filter(pk=procedure.pk).exists())

        imaging_recording.refresh_from_db()
        abr_recording.refresh_from_db()
        self.assertEqual(imaging_recording.mice.count(), 0)
        self.assertEqual(abr_recording.mice.count(), 0)

    def test_delete_mouse_confirmation_warns_about_mouse_less_recordings(self):
        self.create_recordings(self.mouse)

        response = self.client.get(
            reverse("lab:delete_mouse", args=[self.mouse.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Associated recording links will be removed.",
        )
        self.assertContains(response, "Delete mouse")

    def test_procedure_page_does_not_render_delete_selected_action(self):
        response = self.client.get(self.return_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.delete_selected_url)
        self.assertNotContains(response, "Delete selected")

    def test_bulk_edit_page_renders_delete_selected_action(self):
        edit_selected_url = reverse(
            "lab:edit_selected_mice_licence_protocol",
            kwargs={
                "licence_reference": self.protocol.licence_reference,
                "protocol_number": self.protocol.protocol_number,
            },
        )
        response = self.client.post(
            edit_selected_url,
            {
                "return_url": self.return_url,
                "selected_mouse": [self.mouse.pk, self.second_mouse.pk],
            },
        )

        self.assertEqual(response.status_code, 302)
        response = self.client.get(response["Location"])

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.delete_selected_url)
        self.assertContains(response, "Delete selected")

    def test_bulk_delete_removes_mice_and_clears_recording_mouse_links(self):
        first_procedure = Procedure.objects.create(
            mouse=self.mouse,
            procedure_type=self.procedure_type,
        )
        second_procedure = Procedure.objects.create(
            mouse=self.second_mouse,
            procedure_type=self.procedure_type,
        )
        imaging_recording, abr_recording = self.create_recordings(self.mouse)

        response = self.client.post(
            self.delete_selected_url,
            {
                "return_url": self.return_url,
                "selected_mouse": [self.mouse.pk, self.second_mouse.pk],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(self.delete_selected_url, response["Location"])

        response = self.client.post(
            response["Location"],
            {
                "return_url": self.return_url,
                "selected_mouse": [self.mouse.pk, self.second_mouse.pk],
                "confirm_delete": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.return_url)
        self.assertFalse(Mouse.objects.filter(pk=self.mouse.pk).exists())
        self.assertFalse(Mouse.objects.filter(pk=self.second_mouse.pk).exists())
        self.assertTrue(Mouse.objects.filter(pk=self.other_mouse.pk).exists())
        self.assertFalse(
            Procedure.objects.filter(
                pk__in=[first_procedure.pk, second_procedure.pk],
            ).exists()
        )

        imaging_recording.refresh_from_db()
        abr_recording.refresh_from_db()
        self.assertEqual(imaging_recording.mice.count(), 0)
        self.assertEqual(abr_recording.mice.count(), 0)

    def test_bulk_delete_rejects_selected_mouse_outside_protocol(self):
        response = self.client.post(
            self.delete_selected_url,
            {
                "return_url": self.return_url,
                "selected_mouse": [self.mouse.pk, self.other_mouse.pk],
                "confirm_delete": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.return_url)
        self.assertTrue(Mouse.objects.filter(pk=self.mouse.pk).exists())
        self.assertTrue(Mouse.objects.filter(pk=self.other_mouse.pk).exists())


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class AddLitterTests(AuthenticatedTestCase):
    def setUp(self):
        self.protocol = Protocol.objects.create(
            protocol_number=104,
            licence_reference="licence-104",
            name="Protocol 104",
        )
        self.other_protocol = Protocol.objects.create(
            protocol_number=105,
            licence_reference="licence-105",
            name="Protocol 105",
        )
        self.non_regulated_protocol = Protocol.objects.create(
            protocol_number=106,
            licence_reference="licence-106",
            name="Schedule 1",
            allows_regulated_procedures=False,
        )
        self.procedure_type = ProcedureType.objects.create(
            name="Tattooing",
        )
        self.crossing, self.crossing_lines = create_test_crossing(("Gad2",))
        self.add_litter_url = reverse(
            "lab:add_litter_licence_protocol",
            kwargs={
                "licence_reference": self.protocol.licence_reference,
                "protocol_number": self.protocol.protocol_number,
            },
        )
        self.protocol_url = reverse(
            "lab:procedure_page_licence_protocol",
            kwargs={
                "licence_reference": self.protocol.licence_reference,
                "protocol_number": self.protocol.protocol_number,
            },
        )

    def litter_payload(self, **overrides):
        payload = {
            "return_url": self.protocol_url,
            "mouse_count": "2",
            "tattoo_start": "10",
            "date_of_birth": "2026-01-02",
            "breeding_pair": "Line A",
            "sex": "F",
            "status": "Alive",
            "protocol": str(self.protocol.pk),
            "protocol_start_date": "2026-01-03",
            "cull_date": "",
            "severity": "Mild",
            "notes": "New litter",
            "procedure_type": "",
            "procedure_date": "",
            "procedure_lab_member": "",
            "procedure_lab_book_page": "",
            **crossing_post_data(self.crossing, self.crossing_lines),
        }
        payload.update(overrides)
        return payload

    def test_add_litter_form_renders_litter_and_procedure_fields(self):
        response = self.client.get(self.add_litter_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add litter")
        self.assertContains(response, 'name="mouse_count"')
        self.assertContains(response, 'name="tattoo_start"')
        self.assertContains(response, "Local identifier starting number")
        self.assertContains(response, 'name="use_progressive_mouse_ids"')
        self.assertContains(response, 'name="protocol"')
        self.assertContains(response, 'name="crossing_definition"')
        self.assertContains(response, 'name="crossing_search"')
        self.assertNotContains(response, 'name="genotype"')
        self.assertNotContains(response, 'name="crossing"')
        self.assertNotContains(response, 'name="crossing_type"')
        self.assertEqual(
            response.context["form"].fields["protocol"].initial,
            self.protocol,
        )
        self.assertTrue(response.context["can_add_litter_procedure"])
        self.assertContains(response, 'name="procedure_type"')

    def test_add_litter_disables_procedures_for_non_regulated_protocol(self):
        add_litter_url = reverse(
            "lab:add_litter_licence_protocol",
            kwargs={
                "licence_reference": self.non_regulated_protocol.licence_reference,
                "protocol_number": self.non_regulated_protocol.protocol_number,
            },
        )
        response = self.client.get(add_litter_url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_add_litter_procedure"])
        self.assertContains(
            response,
            "Select a protocol that allows regulated procedures before adding procedures.",
        )
        self.assertContains(response, "data-procedure-fieldset")
        self.assertContains(response, "disabled")

        response = self.client.post(
            add_litter_url,
            self.litter_payload(
                protocol=str(self.non_regulated_protocol.pk),
                procedure_type=str(self.procedure_type.pk),
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "This protocol does not allow regulated procedures.",
            response.context["form"].errors["procedure_type"],
        )
        self.assertFalse(Mouse.objects.exists())

    def test_add_litter_creates_progressive_mouse_ids_and_procedures(self):
        response = self.client.post(
            self.add_litter_url,
            self.litter_payload(
                mouse_count="3",
                tattoo_start="10",
                use_progressive_mouse_ids="on",
                mouse_id_prefix="L-",
                mouse_id_start="100",
                procedure_type=str(self.procedure_type.pk),
                procedure_date="2026-07-25",
                procedure_lab_member="AB",
                procedure_lab_book_page="42",
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.protocol_url)

        mice = list(Mouse.objects.filter(protocol=self.protocol).order_by("mouse_id"))
        self.assertEqual(
            [
                mouse.mouse_id
                for mouse in mice
            ],
            ["L-100", "L-101", "L-102"],
        )
        self.assertEqual(
            [
                mouse.tattoo
                for mouse in mice
            ],
            ["10", "11", "12"],
        )
        self.assertTrue(
            all(mouse.crossing_definition == self.crossing for mouse in mice)
        )
        self.assertTrue(
            all(mouse.crossing == "Gad2" for mouse in mice)
        )
        self.assertTrue(
            all(mouse.crossing_type == "Single" for mouse in mice)
        )
        self.assertTrue(
            all(mouse.genotype == "Gad2: Het" for mouse in mice)
        )
        self.assertEqual(
            MouseGenotype.objects.filter(
                mouse__in=mice,
                mouse_line=self.crossing_lines[0],
                zygosity="het",
            ).count(),
            3,
        )
        self.assertTrue(
            all(mouse.protocol == self.protocol for mouse in mice)
        )

        procedures = Procedure.objects.filter(mouse__in=mice)
        self.assertEqual(procedures.count(), 3)
        self.assertTrue(
            all(
                procedure.procedure_type == self.procedure_type
                for procedure in procedures
            )
        )
        self.assertTrue(
            all(procedure.lab_member == "AB" for procedure in procedures)
        )
        self.assertTrue(
            all(procedure.lab_book_page == "42" for procedure in procedures)
        )
        self.assertTrue(
            all(procedure.date == date(2026, 7, 25) for procedure in procedures)
        )

    def test_add_litter_scoped_protocol_cannot_be_changed(self):
        response = self.client.post(
            self.add_litter_url,
            self.litter_payload(
                mouse_count="1",
                tattoo_start="10",
                use_progressive_mouse_ids="on",
                mouse_id_prefix="P104-",
                protocol=str(self.other_protocol.pk),
                procedure_type=str(self.procedure_type.pk),
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.protocol_url)

        mouse = Mouse.objects.get(mouse_id="P104-10")
        self.assertEqual(mouse.protocol, self.protocol)

        procedure = Procedure.objects.get(mouse=mouse)
        self.assertEqual(procedure.procedure_type, self.procedure_type)

    def test_add_litter_without_progressive_ids_uses_existing_mouse_id_rule(self):
        response = self.client.post(
            self.add_litter_url,
            self.litter_payload(
                mouse_count="2",
                tattoo_start="5",
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            list(Mouse.objects.order_by("mouse_id").values_list("mouse_id", flat=True)),
            [
                "20260102_gad2_linea_5",
                "20260102_gad2_linea_6",
            ],
        )

    def test_add_litter_auto_ids_keep_existing_duplicate_suffix_rule(self):
        Mouse.objects.create(
            mouse_id="20260102_gad2_linea_5",
            protocol=self.protocol,
        )

        response = self.client.post(
            self.add_litter_url,
            self.litter_payload(
                mouse_count="1",
                tattoo_start="5",
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Mouse.objects.filter(mouse_id="20260102_gad2_linea_5_1").exists()
        )

    def test_add_litter_rejects_duplicate_progressive_mouse_ids_atomically(self):
        Mouse.objects.create(
            mouse_id="L-100",
            protocol=self.protocol,
        )

        response = self.client.post(
            self.add_litter_url,
            self.litter_payload(
                mouse_count="2",
                tattoo_start="10",
                use_progressive_mouse_ids="on",
                mouse_id_prefix="L-",
                mouse_id_start="100",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Generated mouse IDs already exist: L-100.",
            response.context["form"].errors["mouse_id_prefix"],
        )
        self.assertFalse(Mouse.objects.filter(mouse_id="L-101").exists())
        self.assertEqual(Mouse.objects.count(), 1)


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class EditSelectedMiceTests(AuthenticatedTestCase):
    def setUp(self):
        self.protocol = Protocol.objects.create(
            protocol_number=104,
            licence_reference="licence-104",
            name="Protocol 104",
        )
        self.other_protocol = Protocol.objects.create(
            protocol_number=105,
            licence_reference="licence-105",
            name="Protocol 105",
        )
        self.non_regulated_protocol = Protocol.objects.create(
            protocol_number=106,
            licence_reference="licence-106",
            name="Schedule 1",
            allows_regulated_procedures=False,
        )
        self.procedure_type = ProcedureType.objects.create(
            name="Tattooing",
        )
        self.mouse_1 = Mouse.objects.create(
            mouse_id="M1",
            protocol=self.protocol,
            status="Alive",
            notes="Existing note",
        )
        self.mouse_2 = Mouse.objects.create(
            mouse_id="M2",
            protocol=self.protocol,
            status="Alive",
        )
        self.other_mouse = Mouse.objects.create(
            mouse_id="M3",
            protocol=self.other_protocol,
            status="Alive",
        )
        self.services_project = Project.objects.get(
            name="Services",
            is_service=True,
        )
        self.genotyping_experiment = Experiment.objects.get(
            project=self.services_project,
            name="Genotyping",
        )
        self.add_genotyping_recording_url = reverse(
            "lab:add_recording",
            args=[
                self.services_project.pk,
                self.genotyping_experiment.pk,
            ],
        )
        self.edit_selected_url = reverse(
            "lab:edit_selected_mice_licence_protocol",
            kwargs={
                "licence_reference": self.protocol.licence_reference,
                "protocol_number": self.protocol.protocol_number,
            },
        )
        self.return_url = (
            reverse(
                "lab:procedure_page_licence_protocol",
                kwargs={
                    "licence_reference": self.protocol.licence_reference,
                    "protocol_number": self.protocol.protocol_number,
                },
            )
            + "?sort=mouse_id&page=1"
        )

    def test_no_selection_redirects_without_update(self):
        response = self.client.post(
            self.edit_selected_url,
            {
                "return_url": self.return_url,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.return_url)

    def test_single_selection_redirects_to_mouse_edit(self):
        response = self.client.post(
            self.edit_selected_url,
            {
                "return_url": self.return_url,
                "selected_mouse": [self.mouse_1.pk],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response["Location"].startswith(
                reverse("lab:edit_mouse", args=[self.mouse_1.pk])
            )
        )
        self.assertIn("return_url=", response["Location"])

    def test_multi_selection_opens_bulk_form(self):
        response = self.client.post(
            self.edit_selected_url,
            {
                "return_url": self.return_url,
                "selected_mouse": [self.mouse_1.pk, self.mouse_2.pk],
            },
        )

        self.assertEqual(response.status_code, 302)
        response = self.client.get(response["Location"])

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Edit selected mice")
        self.assertContains(response, self.mouse_1.mouse_id)
        self.assertContains(response, self.mouse_2.mouse_id)
        self.assertContains(response, "Services")
        self.assertContains(response, "Genotyping")
        self.assertContains(response, "Update crossing")
        self.assertContains(response, "Update genotype")
        self.assertContains(response, 'name="crossing_definition"')
        self.assertContains(response, 'name="crossing_search"')
        self.assertNotContains(response, 'name="genotype"')
        self.assertNotContains(response, 'name="crossing"')
        self.assertNotContains(response, 'name="crossing_type"')
        self.assertContains(
            response,
            '<section class="bulk-section">',
        )
        self.assertContains(response, '<h2 class="bulk-title">Services</h2>')
        self.assertContains(
            response,
            "This adds one new procedure record for each selected mouse.",
        )
        content = response.content.decode()
        self.assertLess(
            content.index("This adds one new procedure record"),
            content.index("<h2 class=\"bulk-title\">Services</h2>"),
        )
        self.assertLess(
            content.index("<h2 class=\"bulk-title\">Services</h2>"),
            content.index("Save bulk changes"),
        )
        self.assertNotContains(response, "Set procedure protocol")
        self.assertNotContains(response, "Procedure protocol")

    def test_multi_selection_links_to_service_recording_with_selected_mice(self):
        response = self.client.get(
            self.edit_selected_url,
            {
                "return_url": self.return_url,
                "selected_mouse": [self.mouse_1.pk, self.mouse_2.pk],
            },
        )

        self.assertEqual(response.status_code, 200)
        actions = response.context["service_recording_actions"]
        action = next(
            action
            for action in actions
            if action["title"] == self.genotyping_experiment.name
        )
        action_parts = urlsplit(action["url"])
        action_query = parse_qs(action_parts.query)

        self.assertEqual(action_parts.path, self.add_genotyping_recording_url)
        self.assertEqual(
            action_query["mice"],
            [f"{self.mouse_1.pk},{self.mouse_2.pk}"],
        )

        return_parts = urlsplit(action_query["return_url"][0])
        return_query = parse_qs(return_parts.query)

        self.assertEqual(return_parts.path, self.edit_selected_url)
        self.assertEqual(return_query["return_url"], [self.return_url])
        self.assertEqual(
            return_query["selected_mouse"],
            [self.mouse_1.pk, self.mouse_2.pk],
        )

        add_response = self.client.get(action["url"])

        self.assertEqual(add_response.status_code, 200)
        self.assertEqual(
            [
                mouse.pk
                for mouse in add_response.context["selected_mice"]
            ],
            [self.mouse_1.pk, self.mouse_2.pk],
        )

    def test_bulk_save_updates_mice_and_adds_procedures(self):
        existing_procedure = Procedure.objects.create(
            mouse=self.mouse_1,
            procedure_type=self.procedure_type,
            date=date(2026, 7, 1),
            lab_member="OLD",
            lab_book_page="1",
        )

        response = self.client.post(
            self.edit_selected_url,
            {
                "apply_bulk_edit": "1",
                "return_url": self.return_url,
                "selected_mouse": [self.mouse_1.pk, self.mouse_2.pk],
                "update_status": "on",
                "status": "Culled",
                "append_notes": "on",
                "notes": "Bulk note",
                "procedure_type": self.procedure_type.pk,
                "update_procedure_date": "on",
                "procedure_date": "2026-07-15",
                "update_procedure_lab_member": "on",
                "procedure_lab_member": "AB",
                "update_procedure_lab_book_page": "on",
                "procedure_lab_book_page": "42",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.return_url)

        self.mouse_1.refresh_from_db()
        self.mouse_2.refresh_from_db()
        self.assertEqual(self.mouse_1.status, "Culled")
        self.assertEqual(self.mouse_2.status, "Culled")
        self.assertIn("Existing note", self.mouse_1.notes)
        self.assertIn("Bulk note", self.mouse_1.notes)
        self.assertEqual(self.mouse_2.notes, "Bulk note")

        existing_procedure.refresh_from_db()
        self.assertEqual(existing_procedure.lab_member, "OLD")
        self.assertEqual(existing_procedure.lab_book_page, "1")
        self.assertEqual(existing_procedure.date, date(2026, 7, 1))

        procedure_1 = Procedure.objects.get(
            mouse=self.mouse_1,
            procedure_type=self.procedure_type,
            lab_member="AB",
        )
        procedure_2 = Procedure.objects.get(
            mouse=self.mouse_2,
            procedure_type=self.procedure_type,
        )
        self.assertEqual(procedure_1.lab_member, "AB")
        self.assertEqual(procedure_1.lab_book_page, "42")
        self.assertEqual(procedure_1.date, date(2026, 7, 15))
        self.assertEqual(procedure_2.lab_member, "AB")
        self.assertEqual(procedure_2.lab_book_page, "42")
        self.assertEqual(procedure_2.date, date(2026, 7, 15))
        self.assertEqual(
            Procedure.objects.filter(
                mouse__in=[self.mouse_1, self.mouse_2],
                procedure_type=self.procedure_type,
            ).count(),
            3,
        )
        self.assertEqual(
            Procedure.objects.filter(
                mouse=self.mouse_1,
                procedure_type=self.procedure_type,
            ).count(),
            2,
        )

    def test_bulk_save_updates_structured_crossing_and_genotype_rows(self):
        crossing, lines = create_test_crossing(("Bulk MutA", "Bulk MutB"))

        response = self.client.post(
            self.edit_selected_url,
            {
                "apply_bulk_edit": "1",
                "return_url": self.return_url,
                "selected_mouse": [self.mouse_1.pk, self.mouse_2.pk],
                "update_crossing": "on",
                "crossing_definition": str(crossing.pk),
                "crossing_search": f"Mouse - {crossing.display_name}",
                f"genotype_line_{lines[0].pk}": "hom",
                f"genotype_line_{lines[1].pk}": "wt",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.return_url)

        for mouse in (self.mouse_1, self.mouse_2):
            mouse.refresh_from_db()
            self.assertEqual(mouse.crossing_definition, crossing)
            self.assertEqual(mouse.crossing, "Bulk MutA x Bulk MutB")
            self.assertEqual(mouse.crossing_type, "Multiple")
            self.assertEqual(mouse.genotype, "Bulk MutA: Hom; Bulk MutB: WT")
            self.assertEqual(
                {
                    genotype.mouse_line_id: genotype.zygosity
                    for genotype in mouse.genotype_calls.all()
                },
                {
                    lines[0].pk: "hom",
                    lines[1].pk: "wt",
                },
            )

    def test_bulk_save_updates_genotype_without_reselecting_crossing(self):
        crossing, lines = create_test_crossing(("Bulk OldA", "Bulk OldB"))

        for mouse in (self.mouse_1, self.mouse_2):
            mouse.crossing_definition = crossing
            mouse.crossing = crossing.display_name
            mouse.crossing_type = crossing.inferred_crossing_type
            mouse.genotype = "Bulk OldA: Het; Bulk OldB: Hom"
            mouse.save(
                update_fields=[
                    "crossing_definition",
                    "crossing",
                    "crossing_type",
                    "genotype",
                ],
            )
            MouseGenotype.objects.create(
                mouse=mouse,
                mouse_line=lines[0],
                zygosity="het",
            )
            MouseGenotype.objects.create(
                mouse=mouse,
                mouse_line=lines[1],
                zygosity="hom",
            )

        response = self.client.get(
            self.edit_selected_url,
            {
                "return_url": self.return_url,
                "selected_mouse": [self.mouse_1.pk, self.mouse_2.pk],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, crossing.display_name)
        self.assertContains(response, f"id_genotype_line_{lines[0].pk}")
        self.assertContains(response, f"id_genotype_line_{lines[1].pk}")

        response = self.client.post(
            self.edit_selected_url,
            {
                "apply_bulk_edit": "1",
                "return_url": self.return_url,
                "selected_mouse": [self.mouse_1.pk, self.mouse_2.pk],
                "update_genotype": "on",
                "crossing_definition": str(crossing.pk),
                "crossing_search": f"Mouse - {crossing.display_name}",
                f"genotype_line_{lines[0].pk}": "",
                f"genotype_line_{lines[1].pk}": "wt",
            },
        )

        self.assertEqual(response.status_code, 302)

        for mouse in (self.mouse_1, self.mouse_2):
            mouse.refresh_from_db()
            self.assertEqual(mouse.crossing_definition, crossing)
            self.assertEqual(mouse.crossing, "Bulk OldA x Bulk OldB")
            self.assertEqual(mouse.crossing_type, "Multiple")
            self.assertEqual(mouse.genotype, "Bulk OldA: Het; Bulk OldB: WT")
            self.assertEqual(
                {
                    genotype.mouse_line_id: genotype.zygosity
                    for genotype in mouse.genotype_calls.all()
                },
                {
                    lines[0].pk: "het",
                    lines[1].pk: "wt",
                },
            )

    def test_bulk_save_rejects_invalid_selected_mouse_atomically(self):
        response = self.client.post(
            self.edit_selected_url,
            {
                "apply_bulk_edit": "1",
                "return_url": self.return_url,
                "selected_mouse": [self.mouse_1.pk, self.other_mouse.pk],
                "update_status": "on",
                "status": "Culled",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.return_url)

        self.mouse_1.refresh_from_db()
        self.other_mouse.refresh_from_db()
        self.assertEqual(self.mouse_1.status, "Alive")
        self.assertEqual(self.other_mouse.status, "Alive")

    def test_bulk_save_rejects_invalid_form_atomically(self):
        response = self.client.post(
            self.edit_selected_url,
            {
                "apply_bulk_edit": "1",
                "return_url": self.return_url,
                "selected_mouse": [self.mouse_1.pk, self.mouse_2.pk],
                "update_status": "on",
                "status": "X" * 51,
            },
        )

        self.assertEqual(response.status_code, 200)

        self.mouse_1.refresh_from_db()
        self.mouse_2.refresh_from_db()
        self.assertEqual(self.mouse_1.status, "Alive")
        self.assertEqual(self.mouse_2.status, "Alive")

    def test_bulk_save_rejects_non_regulated_protocol_when_procedures_exist(self):
        Procedure.objects.create(
            mouse=self.mouse_1,
            procedure_type=self.procedure_type,
        )

        response = self.client.post(
            self.edit_selected_url,
            {
                "apply_bulk_edit": "1",
                "return_url": self.return_url,
                "selected_mouse": [self.mouse_1.pk, self.mouse_2.pk],
                "update_protocol": "on",
                "protocol": self.non_regulated_protocol.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Remove existing procedures before moving these mice to a protocol "
            "that does not allow regulated procedures: M1",
        )

        self.mouse_1.refresh_from_db()
        self.mouse_2.refresh_from_db()
        self.assertEqual(self.mouse_1.protocol, self.protocol)
        self.assertEqual(self.mouse_2.protocol, self.protocol)

    def test_single_edit_does_not_render_mouse_id_input(self):
        response = self.client.get(
            reverse("lab:edit_mouse", args=[self.mouse_1.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mouse ID")
        self.assertContains(response, self.mouse_1.mouse_id)
        self.assertContains(response, "Local identifier")
        self.assertNotContains(response, 'name="mouse_id"')

    def test_single_mouse_edit_links_to_service_recording_with_mouse_selected(self):
        edit_url = reverse("lab:edit_mouse", args=[self.mouse_1.pk])
        response = self.client.get(
            edit_url,
            {
                "return_url": self.return_url,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Services")
        self.assertContains(response, "Genotyping")

        actions = response.context["service_recording_actions"]
        action = next(
            action
            for action in actions
            if action["title"] == self.genotyping_experiment.name
        )
        action_parts = urlsplit(action["url"])
        action_query = parse_qs(action_parts.query)

        self.assertEqual(action_parts.path, self.add_genotyping_recording_url)
        self.assertEqual(action_query["mice"], [self.mouse_1.pk])

        return_parts = urlsplit(action_query["return_url"][0])
        return_query = parse_qs(return_parts.query)

        self.assertEqual(return_parts.path, edit_url)
        self.assertEqual(return_query["return_url"], [self.return_url])

        add_response = self.client.get(action["url"])

        self.assertEqual(add_response.status_code, 200)
        self.assertEqual(
            [
                mouse.pk
                for mouse in add_response.context["selected_mice"]
            ],
            [self.mouse_1.pk],
        )

    def test_single_mouse_edit_renders_existing_dates_for_browser_inputs(self):
        self.mouse_1.date_of_birth = date(2026, 1, 2)
        self.mouse_1.protocol_start_date = date(2026, 1, 3)
        self.mouse_1.cull_date = date(2026, 1, 4)
        self.mouse_1.save(
            update_fields=[
                "date_of_birth",
                "protocol_start_date",
                "cull_date",
            ],
        )

        response = self.client.get(
            reverse("lab:edit_mouse", args=[self.mouse_1.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="date_of_birth" value="2026-01-02"')
        self.assertContains(
            response,
            'name="protocol_start_date" value="2026-01-03"',
        )
        self.assertContains(response, 'name="cull_date" value="2026-01-04"')

    def test_procedure_model_does_not_have_protocol_field(self):
        self.assertNotIn(
            "protocol",
            [
                field.name
                for field in Procedure._meta.get_fields()
            ],
        )

    def test_single_mouse_edit_saves_mouse_fields_alone(self):
        crossing, lines = create_test_crossing(("Gad2",))
        response = self.client.post(
            reverse("lab:edit_mouse", args=[self.mouse_1.pk]),
            {
                "save_mouse": "1",
                "return_url": self.return_url,
                "date_of_birth": "",
                "tattoo": "",
                "genotype": "Gad2",
                "crossing": "",
                "crossing_type": "",
                "sex": "F",
                "status": "Culled",
                "protocol_start_date": "",
                "cull_date": "",
                "protocol": self.protocol.pk,
                "severity": "Mild",
                "notes": "Updated mouse note",
                **crossing_post_data(crossing, lines),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.return_url)

        self.mouse_1.refresh_from_db()
        self.assertEqual(self.mouse_1.crossing_definition, crossing)
        self.assertEqual(self.mouse_1.crossing, "Gad2")
        self.assertEqual(self.mouse_1.crossing_type, "Single")
        self.assertEqual(self.mouse_1.genotype, "Gad2: Het")
        self.assertEqual(
            list(
                self.mouse_1.genotype_calls.values_list(
                    "mouse_line",
                    "zygosity",
                )
            ),
            [(lines[0].pk, "het")],
        )
        self.assertEqual(self.mouse_1.sex, "F")
        self.assertEqual(self.mouse_1.status, "Culled")
        self.assertEqual(self.mouse_1.severity, "Mild")
        self.assertEqual(self.mouse_1.notes, "Updated mouse note")
        self.assertFalse(
            Procedure.objects.filter(mouse=self.mouse_1).exists()
        )

    def test_single_mouse_edit_groups_genotype_fields_after_crossing(self):
        crossing, lines = create_test_crossing(("MutA", "MutB"))
        self.mouse_1.crossing_definition = crossing
        self.mouse_1.crossing = crossing.display_name
        self.mouse_1.crossing_type = crossing.inferred_crossing_type
        self.mouse_1.genotype = "MutA: Het; MutB: Hom"
        self.mouse_1.save(
            update_fields=[
                "crossing_definition",
                "crossing",
                "crossing_type",
                "genotype",
            ],
        )
        MouseGenotype.objects.create(
            mouse=self.mouse_1,
            mouse_line=lines[0],
            zygosity="het",
        )
        MouseGenotype.objects.create(
            mouse=self.mouse_1,
            mouse_line=lines[1],
            zygosity="hom",
        )

        response = self.client.get(
            reverse("lab:edit_mouse", args=[self.mouse_1.pk]),
        )
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Genotype")
        self.assertContains(response, 'data-genotype-subset="true"')
        self.assertLess(
            content.index("id_crossing_search"),
            content.index("Genotype"),
        )
        self.assertLess(
            content.index("Genotype"),
            content.index(f"id_genotype_line_{lines[0].pk}"),
        )
        self.assertLess(
            content.index(f"id_genotype_line_{lines[1].pk}"),
            content.index("id_sex"),
        )

    def test_single_mouse_edit_updates_structured_crossing_and_genotype_rows(self):
        initial_crossing, initial_lines = create_test_crossing(("MutA", "MutB"))
        new_crossing, new_lines = create_test_crossing(("MutB", "MutC"))
        initial_lines_by_name = {
            line.name: line
            for line in initial_lines
        }
        new_lines_by_name = {
            line.name: line
            for line in new_lines
        }
        self.mouse_1.crossing_definition = initial_crossing
        self.mouse_1.crossing = initial_crossing.display_name
        self.mouse_1.crossing_type = initial_crossing.inferred_crossing_type
        self.mouse_1.genotype = "MutA: Het; MutB: Hom"
        self.mouse_1.save(
            update_fields=[
                "crossing_definition",
                "crossing",
                "crossing_type",
                "genotype",
            ],
        )
        MouseGenotype.objects.create(
            mouse=self.mouse_1,
            mouse_line=initial_lines_by_name["MutA"],
            zygosity="het",
        )
        MouseGenotype.objects.create(
            mouse=self.mouse_1,
            mouse_line=initial_lines_by_name["MutB"],
            zygosity="hom",
        )

        response = self.client.post(
            reverse("lab:edit_mouse", args=[self.mouse_1.pk]),
            {
                "save_mouse": "1",
                "return_url": self.return_url,
                "date_of_birth": "",
                "tattoo": "",
                "breeding_pair": "",
                "sex": "F",
                "status": "Alive",
                "protocol_start_date": "",
                "cull_date": "",
                "protocol": self.protocol.pk,
                "severity": "",
                "notes": "Changed crossing",
                "crossing_definition": str(new_crossing.pk),
                "crossing_search": f"Mouse - {new_crossing.display_name}",
                f"genotype_line_{new_lines_by_name['MutC'].pk}": "wt",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.mouse_1.refresh_from_db()
        self.assertEqual(self.mouse_1.crossing_definition, new_crossing)
        self.assertEqual(self.mouse_1.crossing, "MutB x MutC")
        self.assertEqual(self.mouse_1.crossing_type, "Multiple")
        self.assertEqual(self.mouse_1.genotype, "MutB: Hom; MutC: WT")
        self.assertEqual(
            {
                genotype.mouse_line_id: genotype.zygosity
                for genotype in self.mouse_1.genotype_calls.all()
            },
            {
                new_lines_by_name["MutB"].pk: "hom",
                new_lines_by_name["MutC"].pk: "wt",
            },
        )

    def test_single_mouse_edit_can_preserve_legacy_free_text_genetics(self):
        legacy_mouse = Mouse.objects.create(
            mouse_id="LEGACY-GENETICS",
            protocol=self.protocol,
            crossing="Legacy line",
            crossing_type="Single",
            genotype="Legacy genotype",
            status="Alive",
        )
        edit_url = reverse("lab:edit_mouse", args=[legacy_mouse.pk])

        response = self.client.get(edit_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Existing free-text genetics")
        self.assertContains(response, "Legacy line")
        self.assertContains(response, "Legacy genotype")

        response = self.client.post(
            edit_url,
            {
                "save_mouse": "1",
                "return_url": self.return_url,
                "date_of_birth": "",
                "tattoo": "",
                "breeding_pair": "",
                "sex": "",
                "status": "Culled",
                "protocol_start_date": "",
                "cull_date": "",
                "protocol": self.protocol.pk,
                "severity": "",
                "notes": "Legacy note",
                "crossing_definition": "",
                "crossing_search": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        legacy_mouse.refresh_from_db()
        self.assertIsNone(legacy_mouse.crossing_definition)
        self.assertEqual(legacy_mouse.crossing, "Legacy line")
        self.assertEqual(legacy_mouse.crossing_type, "Single")
        self.assertEqual(legacy_mouse.genotype, "Legacy genotype")
        self.assertEqual(legacy_mouse.status, "Culled")

        response = self.client.get(reverse("lab:mouse_record", args=[legacy_mouse.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Legacy line")
        self.assertContains(response, "Legacy genotype")

    def test_single_mouse_edit_rejects_non_regulated_protocol_when_procedures_exist(self):
        Procedure.objects.create(
            mouse=self.mouse_1,
            procedure_type=self.procedure_type,
        )

        response = self.client.post(
            reverse("lab:edit_mouse", args=[self.mouse_1.pk]),
            {
                "save_mouse": "1",
                "return_url": self.return_url,
                "date_of_birth": "",
                "tattoo": "",
                "breeding_pair": "",
                "genotype": "Gad2",
                "crossing": "",
                "crossing_type": "",
                "sex": "F",
                "status": "Alive",
                "protocol_start_date": "",
                "cull_date": "",
                "protocol": self.non_regulated_protocol.pk,
                "severity": "Mild",
                "notes": "Updated mouse note",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            (
                "Remove existing procedures before moving this mouse to a "
                "protocol that does not allow regulated procedures."
            ),
            response.context["mouse_form"].errors["protocol"],
        )

        self.mouse_1.refresh_from_db()
        self.assertEqual(self.mouse_1.protocol, self.protocol)

    def test_single_mouse_edit_creates_missing_procedure(self):
        response = self.client.post(
            reverse("lab:edit_mouse", args=[self.mouse_1.pk]),
            {
                "save_procedure": "1",
                "return_url": self.return_url,
                "procedure_type": self.procedure_type.pk,
                "date": "2026-07-20",
                "lab_member": "AB",
                "lab_book_page": "42",
            },
        )

        self.assertEqual(response.status_code, 302)
        procedure = Procedure.objects.get(
            mouse=self.mouse_1,
            procedure_type=self.procedure_type,
        )
        self.assertEqual(procedure.date, date(2026, 7, 20))
        self.assertEqual(procedure.lab_member, "AB")
        self.assertEqual(procedure.lab_book_page, "42")

    def test_single_mouse_edit_adds_duplicate_procedure(self):
        existing_procedure = Procedure.objects.create(
            mouse=self.mouse_1,
            procedure_type=self.procedure_type,
            date=date(2026, 7, 1),
            lab_member="OLD",
            lab_book_page="1",
        )

        response = self.client.post(
            reverse("lab:edit_mouse", args=[self.mouse_1.pk]),
            {
                "save_procedure": "1",
                "return_url": self.return_url,
                "procedure_type": self.procedure_type.pk,
                "date": "2026-07-21",
                "lab_member": "CD",
                "lab_book_page": "99",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Procedure.objects.filter(
                mouse=self.mouse_1,
                procedure_type=self.procedure_type,
            ).count(),
            2,
        )
        existing_procedure.refresh_from_db()
        self.assertEqual(existing_procedure.date, date(2026, 7, 1))
        self.assertEqual(existing_procedure.lab_member, "OLD")
        self.assertEqual(existing_procedure.lab_book_page, "1")

        new_procedure = Procedure.objects.exclude(
            pk=existing_procedure.pk,
        ).get(
            mouse=self.mouse_1,
            procedure_type=self.procedure_type,
        )
        self.assertEqual(new_procedure.date, date(2026, 7, 21))
        self.assertEqual(new_procedure.lab_member, "CD")
        self.assertEqual(new_procedure.lab_book_page, "99")

    def test_mouse_edit_renders_procedure_date_edit_and_delete_buttons(self):
        Procedure.objects.create(
            mouse=self.mouse_1,
            procedure_type=self.procedure_type,
            date=date(2026, 7, 20),
            lab_member="AB",
            lab_book_page="42",
        )

        response = self.client.get(
            reverse("lab:edit_mouse", args=[self.mouse_1.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "20/07/2026")
        self.assertNotContains(response, "<th>Protocol</th>", html=True)
        self.assertContains(response, "Edit")
        self.assertContains(response, "Delete")

    def test_edit_mouse_procedure_updates_selected_row(self):
        procedure_to_edit = Procedure.objects.create(
            mouse=self.mouse_1,
            procedure_type=self.procedure_type,
            date=date(2026, 7, 20),
            lab_member="AB",
            lab_book_page="42",
        )
        other_procedure = Procedure.objects.create(
            mouse=self.mouse_1,
            procedure_type=self.procedure_type,
            date=date(2026, 7, 22),
            lab_member="EF",
            lab_book_page="88",
        )
        edit_url = reverse(
            "lab:edit_mouse_procedure",
            args=[self.mouse_1.pk, procedure_to_edit.pk],
        )

        edit_response = self.client.get(edit_url)
        self.assertEqual(edit_response.status_code, 200)
        self.assertNotContains(edit_response, 'name="protocol"')

        response = self.client.post(
            edit_url,
            {
                "return_url": self.return_url,
                "procedure_type": self.procedure_type.pk,
                "date": "2026-07-21",
                "lab_member": "CD",
                "lab_book_page": "99",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response["Location"].startswith(
                reverse("lab:edit_mouse", args=[self.mouse_1.pk])
            )
        )

        procedure_to_edit.refresh_from_db()
        other_procedure.refresh_from_db()
        self.assertEqual(procedure_to_edit.date, date(2026, 7, 21))
        self.assertEqual(procedure_to_edit.lab_member, "CD")
        self.assertEqual(procedure_to_edit.lab_book_page, "99")
        self.assertEqual(other_procedure.date, date(2026, 7, 22))
        self.assertEqual(other_procedure.lab_member, "EF")
        self.assertEqual(other_procedure.lab_book_page, "88")

    def test_mouse_record_renders_procedure_date(self):
        Procedure.objects.create(
            mouse=self.mouse_1,
            procedure_type=self.procedure_type,
            date=date(2026, 7, 20),
            lab_member="AB",
            lab_book_page="42",
        )

        response = self.client.get(
            reverse("lab:mouse_record", args=[self.mouse_1.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "20/07/2026")

    def test_delete_mouse_procedure_removes_database_row(self):
        procedure = Procedure.objects.create(
            mouse=self.mouse_1,
            procedure_type=self.procedure_type,
            date=date(2026, 7, 20),
            lab_member="AB",
            lab_book_page="42",
        )

        response = self.client.post(
            reverse(
                "lab:delete_mouse_procedure",
                args=[self.mouse_1.pk, procedure.pk],
            ),
            {
                "return_url": self.return_url,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            Procedure.objects.filter(pk=procedure.pk).exists()
        )
