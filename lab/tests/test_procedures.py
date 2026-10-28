from datetime import date
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from ..models import Mouse, Procedure, ProcedureType, Protocol
from ..test_navigation import Markup
from ._support import AuthenticatedTestCase, create_test_crossing, crossing_post_data


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
        animal_summary_url = reverse(
            "lab:animal_summary",
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
        self.assertContains(response, animal_summary_url)

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
        self.assertNotContains(response, "data-table-select-all-button")
        self.assertContains(response, "data-select-all")
        self.assertContains(response, "data-selection-count")
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
        self.assertEqual(response.context["record_count"]["end"], 32)
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

    def test_current_year_filter_is_unchecked_by_default(self):
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

        self.assertFalse(current_year_field["checked"])
        self.assertContains(response, self.mouse_a1.mouse_id)
        self.assertContains(response, self.old_mouse_a1.mouse_id)

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

    def test_checked_current_year_filter_excludes_previous_year_animals(self):
        response = self.client.get(
            reverse(
                "lab:procedure_page_licence_protocol",
                kwargs={
                    "licence_reference": self.licence_a,
                    "protocol_number": self.protocol_a1.protocol_number,
                },
            )
            + "?show_current_year=1"
        )
        current_year_field = next(
            field
            for field in response.context["filter_form"]["fields"]
            if field["name"] == "show_current_year"
        )

        self.assertTrue(current_year_field["checked"])
        self.assertContains(response, self.mouse_a1.mouse_id)
        self.assertNotContains(response, self.old_mouse_a1.mouse_id)

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
            + "?show_current_year=1"
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
            lab_member="EARLIER-MEMBER",
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
        self.assertNotContains(response, "EARLIER-MEMBER")
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
            "lab:add_litter_licence_protocol",
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
            "lab:view_selected_mice_licence_protocol",
            kwargs={
                "licence_reference": self.licence_a,
                "protocol_number": self.protocol_a1.protocol_number,
            },
        )

        self.assertNotContains(response, "Add mouse")
        self.assertContains(response, f'href="{add_litter_url}?return_url=')
        self.assertContains(response, f'action="{edit_selected_url}"')
        self.assertContains(response, 'data-animal-action="selected-mice-form"', count=1)
        self.assertContains(response, 'data-animal-selected-label="View selected"')
        markup = Markup(response.content.decode())
        all_animals = next(link for link in markup.find('a') if link.text.strip() == 'View all animals')
        selected_action = markup.find('a', **{'data-animal-action': 'selected-mice-form'})[0]
        self.assertEqual(all_animals.attrs['href'], reverse('lab:mice_page'))
        self.assertNotIn('button', all_animals.attrs.get('class', ''))
        self.assertIs(all_animals.parent, selected_action.parent)
        self.assertNotContains(response, 'form="selected-mice-form"')

    def test_add_mouse_scoped_protocol_cannot_be_changed(self):
        crossing, lines = create_test_crossing(("Scoped",))
        add_mouse_url = reverse(
            "lab:add_litter_licence_protocol",
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
                "mouse_id_prefix": "MOUSE-SCOPED-ADD",
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
