from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from ..models import Crossing, CrossingLine, Mouse, MouseGenotype, MouseLine, Protocol, Species, resolve_crossing
from ._support import AuthenticatedTestCase, create_test_crossing


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
                "1 animal genotype record."
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
                "1 animal uses it."
            ),
        )
