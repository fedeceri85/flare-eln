import json
from datetime import date
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from ..models import Mouse, Procedure, ProcedureType, Protocol
from ..views.common import url_with_query_params
from ._support import AuthenticatedTestCase, create_test_crossing, crossing_post_data


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
        self.add_mouse_url = reverse("lab:add_litter_all")
        self.add_litter_url = reverse("lab:add_litter_all")

    def test_reports_page_renders_active_mice_card(self):
        response = self.client.get(reverse("lab:reports_page"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Animals")
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

    def test_mice_page_defaults_to_date_added_descending_with_unknown_last(self):
        for mouse, added in ((self.recent_mouse, date(2026, 1, 1)), (self.old_mouse, date(2026, 2, 1)),
                             (self.non_regulated_mouse, date(2026, 2, 1)), (self.no_dob_mouse, None)):
            Mouse.objects.filter(pk=mouse.pk).update(date_added=added)
        expected = [self.non_regulated_mouse.pk, self.old_mouse.pk, self.recent_mouse.pk, self.no_dob_mouse.pk]
        for params, order in (({}, expected), ({'sort': 'invalid'}, expected),
                              ({'sort': 'date_added', 'direction': 'asc'}, [self.recent_mouse.pk, self.non_regulated_mouse.pk, self.old_mouse.pk, self.no_dob_mouse.pk])):
            response = self.client.get(self.mice_url, params)
            rows = response.context['table']['rows']
            self.assertEqual([row['cells'][0]['checkbox']['value'] for row in rows], order)
        response = self.client.get(self.mice_url, {'sort': 'date_of_birth', 'direction': 'desc'})
        self.assertEqual(response.context['table']['rows'][0]['cells'][0]['checkbox']['value'], self.recent_mouse.pk)

    def test_mice_page_renders_admin_style_columns_and_actions(self):
        response = self.client.get(self.mice_url)

        self.assertEqual(response.status_code, 200)
        for label in (
            "Animal ID",
            "Date added",
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
        self.assertContains(response, "Add animals")
        self.assertContains(response, 'data-animal-action="selected-mice-form"', count=1)
        self.assertNotContains(response, 'form="selected-mice-form"')
        self.assertNotContains(response, "Edit animal")
        self.assertContains(response, "Select all")
        self.assertContains(response, "data-table-select-all-button")
        self.assertContains(response, 'name="per_page"')
        self.assertEqual(response.context["page_size_control"]["current"], 25)
        for page_size in (25, 50, 100, 150, 200):
            self.assertContains(response, f'value="{page_size}"')
        self.assertContains(
            response,
            reverse("lab:animal_summary", args=[self.recent_mouse.pk]),
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
        self.assertNotContains(response, "Local ID type")
        self.assertIsNone(response.context["form"].fields["protocol"].initial)

        response = self.client.post(
            self.add_mouse_url,
            {
                "return_url": self.mice_url,
                "mouse_id_prefix": "MICE-ADDED-NO-PROTOCOL",
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
        self.assertEqual(response["Location"], url_with_query_params(
            reverse("lab:view_selected_mice_all"), selected_mouse=["MICE-ADDED-NO-PROTOCOL"],
            return_url=self.mice_url,
        ))

        mouse = Mouse.objects.get(pk="MICE-ADDED-NO-PROTOCOL")
        self.assertEqual(mouse.protocol, self.non_regulated_protocol)
        self.assertEqual(mouse.genotype, "Added: Het")

    def test_add_mouse_saves_structured_crossing_and_genotype_calls(self):
        crossing, lines = create_test_crossing(("Mut1", "Mut2"))

        response = self.client.post(
            self.add_mouse_url,
            {
                "return_url": self.mice_url,
                "mouse_id_prefix": "MICE-STRUCTURED-ADD",
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
                "no_id_available": "on",
                "mouse_numbers": "1",
                "mouse_id_prefix": "",
                "date_of_birth": "2026-01-02",
                "tattoo": "7",
                "marking_type": "tattoo",
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
            Mouse.objects.filter(mouse_id="260102_Mut1Mu_linea_1").exists()
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
                "base_id": "260102_Mut1Mu_linea_7",
                "final_id": "260102_Mut1Mu_linea_7",
            },
        )

    def test_add_mouse_dynamic_genotype_subset_script_is_available(self):
        response = self.client.get(self.add_mouse_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'src="/static/lab/crossing_selector.js"')
        self.assertContains(response, "data-genotype-fields-url")
        self.assertContains(response, "data-crossing-lookup-input")
        self.assertContains(response, "data-crossing-definition-input")

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
                "mouse_numbers": "30-31",
                "mouse_id_prefix": "LITTER-",
                **{f"pup_{number}_genotype_line_{line.pk}": "het" for number in (30, 31) for line in lines},
                "date_of_birth": "",
                "sex": "",
                "status": "Alive",
                "protocol": str(self.protocol.pk),
                "protocol_start_date": "",
                "cull_date": "",
                "severity": "",
                "notes": "No protocol litter",
                "procedure_drafts": json.dumps([{
                    "procedure_type": self.procedure_type.pk, "date": "2026-07-29",
                    "lab_member": "AB", "lab_book_page": "10",
                }]),
                **crossing_post_data(crossing, lines),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], url_with_query_params(
            reverse("lab:view_selected_mice_all"), selected_mouse=["LITTER-30", "LITTER-31"],
            return_url=self.mice_url,
        ))

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
                reverse("lab:edit_selected_mice_all")
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
        self.assertContains(response, "All animals.")
        self.assertContains(response, self.recent_mouse.mouse_id)
        self.assertContains(response, self.old_mouse.mouse_id)
        self.assertContains(response, self.non_regulated_mouse.mouse_id)
        self.assertContains(
            response,
            "Regulated procedure creation is disabled for one or more selected animals.",
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
                "status": "Culled",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.mice_url)

        self.recent_mouse.refresh_from_db()
        self.old_mouse.refresh_from_db()
        self.non_regulated_mouse.refresh_from_db()
        self.no_dob_mouse.refresh_from_db()
        self.assertEqual(self.recent_mouse.status, "Culled")
        self.assertEqual(self.old_mouse.status, "Culled")
        self.assertEqual(self.non_regulated_mouse.status, "Culled")
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
