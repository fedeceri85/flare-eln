import json
from datetime import date
from django.test import override_settings
from django.urls import reverse
from ..utils import parse_mouse_numbers
from ..views.common import url_with_query_params
from ..models import Mouse, MouseGenotype, Procedure, ProcedureType, Protocol
from ._support import AuthenticatedTestCase, create_test_crossing, crossing_post_data


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
            "mouse_numbers": "10-11",
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
        for number in parse_mouse_numbers(payload["mouse_numbers"]):
            payload[f"pup_{number}_sex"] = "F"
            for line in self.crossing_lines:
                payload[f"pup_{number}_genotype_line_{line.pk}"] = "het"
        return payload

    def test_status_defaults_to_alive_and_allows_creating_culled_animals(self):
        response = self.client.get(self.add_litter_url)
        self.assertEqual(response.context["form"]["status"].value(), "Alive")
        self.assertIn('<select ', str(response.context["form"]["status"]))
        self.assertContains(response, '<option value="Alive" selected>Alive</option>', html=True)
        response = self.client.post(self.add_litter_url, self.litter_payload(status="Culled"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(Mouse.objects.values_list("status", flat=True)), ["Culled", "Culled"])

    def test_creation_rejects_blank_or_unrecognised_status_without_creating_mice(self):
        for status in ("", "Archived"):
            with self.subTest(status=status):
                response = self.client.post(self.add_litter_url, self.litter_payload(status=status))
                self.assertEqual(response.status_code, 200)
                self.assertIn("status", response.context["form"].errors)
                self.assertFalse(Mouse.objects.exists())

    def test_add_litter_form_renders_litter_and_procedure_fields(self):
        response = self.client.get(self.add_litter_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add animals")
        self.assertContains(response, 'name="mouse_numbers"')
        self.assertContains(response, 'name="mouse_id_prefix"')
        self.assertContains(response, 'name="no_id_available"')
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
        self.assertContains(response, 'name="new_procedure-procedure_type"')

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
                procedure_drafts=json.dumps([{"procedure_type": self.procedure_type.pk}]),
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "This protocol does not allow regulated procedures.",
            response.context["form"].errors["procedure_drafts"],
        )
        self.assertFalse(Mouse.objects.exists())

    def test_add_litter_creates_progressive_mouse_ids_and_procedures(self):
        response = self.client.post(
            self.add_litter_url,
            self.litter_payload(
                mouse_numbers="100-102",
                mouse_id_prefix="L-",
                procedure_drafts=json.dumps([{
                    "procedure_type": self.procedure_type.pk, "date": "2026-07-25",
                    "lab_member": "AB", "lab_book_page": "42",
                }]),
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], url_with_query_params(
            reverse("lab:view_selected_mice_all"), selected_mouse=["L-100", "L-101", "L-102"],
            return_url=self.protocol_url,
        ))

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
            ["", "", ""],
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
                mouse_numbers="10",
                mouse_id_prefix="P104-",
                protocol=str(self.other_protocol.pk),
                procedure_drafts=json.dumps([{"procedure_type": self.procedure_type.pk}]),
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], url_with_query_params(
            reverse("lab:view_selected_mice_all"), selected_mouse=["P104-10"],
            return_url=self.protocol_url,
        ))

        mouse = Mouse.objects.get(mouse_id="P104-10")
        self.assertEqual(mouse.protocol, self.protocol)

        procedure = Procedure.objects.get(mouse=mouse)
        self.assertEqual(procedure.procedure_type, self.procedure_type)

    def test_add_litter_generates_visible_prefix_when_no_id_available(self):
        response = self.client.post(
            self.add_litter_url,
            self.litter_payload(
                mouse_numbers="5-6",
                no_id_available="on",
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            list(Mouse.objects.order_by("mouse_id").values_list("mouse_id", flat=True)),
            [
                "260102_Gad2_linea_5",
                "260102_Gad2_linea_6",
            ],
        )

    def test_add_litter_rejects_duplicate_generated_ids(self):
        Mouse.objects.create(mouse_id="260102_Gad2_linea_5", protocol=self.protocol)
        response = self.client.post(self.add_litter_url, self.litter_payload(
            mouse_numbers="5", no_id_available="on",
        ))
        self.assertEqual(response.status_code, 200)
        self.assertIn("mouse_numbers", response.context["form"].errors)
        self.assertEqual(Mouse.objects.count(), 1)

    def test_add_litter_rejects_duplicate_progressive_mouse_ids_atomically(self):
        Mouse.objects.create(
            mouse_id="L-100",
            protocol=self.protocol,
        )

        response = self.client.post(
            self.add_litter_url,
            self.litter_payload(
                mouse_numbers="100-101",
                mouse_id_prefix="L-",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Animal IDs already exist: L-100.",
            response.context["form"].errors["mouse_numbers"],
        )
        self.assertFalse(Mouse.objects.filter(mouse_id="L-101").exists())
        self.assertEqual(Mouse.objects.count(), 1)
