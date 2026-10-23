from datetime import date
from urllib.parse import parse_qs, urlencode, urlsplit
from django.test import override_settings
from django.urls import reverse
from ..test_navigation import Markup
from ..forms import individual_mouse_field_prefix, sync_mouse_structured_genetics
from ..models import Experiment, Mouse, MouseGenotype, Procedure, ProcedureType, Project, Protocol
from ._support import AuthenticatedTestCase, create_test_crossing, crossing_post_data


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

    def test_single_selection_uses_unified_editor(self):
        response = self.client.post(self.edit_selected_url, {"selected_mouse": [self.mouse_1.pk], "return_url": self.return_url})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response["Location"]).path, self.edit_selected_url)
        self.assertEqual(parse_qs(urlsplit(response["Location"]).query)["selected_mouse"], [self.mouse_1.pk])


    def test_multi_selection_opens_tabbed_bulk_form_with_independent_procedures(self):
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
        self.assertContains(response, "Edit selected animals")
        for mouse in (self.mouse_1, self.mouse_2):
            self.assertContains(response, mouse.mouse_id)

        html = Markup(response.content.decode())
        for key in ("shared", "individual", "procedures", "services"):
            self.assertEqual(len(html.find("button", role="tab", **{"data-mouse-tab": key})), 1)
            self.assertTrue(html.find("section", **{"data-mouse-tab-panel": key}))
        for name in ("update_crossing", "update_genotype", "crossing_definition", "crossing_search"):
            self.assertTrue(html.find("input", name=name))
        for name in ("genotype", "crossing", "crossing_type"):
            self.assertFalse(html.find("input", name=name))

        saves = [button for button in html.find("button") if button.text.strip() == "Save bulk changes"]
        self.assertEqual(len(saves), 1)
        self.assertEqual(saves[0].attrs.get("form"), "primary-form")
        self.assertTrue(saves[0].within("div", **{"data-page-actions": None}))
        self.assertFalse(saves[0].within("form"))

        adds = [button for button in html.find("button") if button.text.strip() == "Add procedure"]
        self.assertEqual(len(adds), 1)
        self.assertTrue(adds[0].within("form", id="new-procedure-form"))
        self.assertFalse(adds[0].within("form", id="primary-form"))
        self.assertTrue(adds[0].within("section", id="mouse-panel-procedures"))
        service_cells = [cell for cell in html.find("td") if cell.text.strip() == "Genotyping"
                         and cell.within("section", id="mouse-panel-services")]
        self.assertEqual(len(service_cells), 1)
        self.assertTrue(any(link.text.strip() == "Add record" and link.parent.parent is service_cells[0].parent
                            for link in html.find("a")))
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

    def test_bulk_save_updates_mice_without_adding_procedures(self):
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
                "notes_action": "append",
                "notes": "Bulk note",
                "new_procedure-procedure_type": self.procedure_type.pk,
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

        self.assertEqual(Procedure.objects.count(), 1)
        self.assertEqual(self.mouse_2.procedures.count(), 0)

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

    def test_status_dropdown_prefills_current_status_for_single_and_matching_mice(self):
        for mouse in (self.mouse_1, self.mouse_2):
            mouse.status = "Culled"
            mouse.save(update_fields=["status"])
        for selection in ([self.mouse_1.pk], [self.mouse_1.pk, self.mouse_2.pk]):
            response = self.client.get(self.edit_selected_url, {"selected_mouse": selection})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["form"]["status"].value(), "Culled")
            self.assertIn('<select ', str(response.context["form"]["status"]))
            self.assertContains(response, '<option value="Culled" selected>Culled</option>', html=True)

    def test_bulk_status_change_requires_valid_choice_and_rejects_all_changes_atomically(self):
        for status in ("", "Archived"):
            with self.subTest(status=status):
                response = self.client.post(self.edit_selected_url, {
                    "apply_bulk_edit": "1", "selected_mouse": [self.mouse_1.pk, self.mouse_2.pk],
                    "update_status": "on", "status": status,
                    "notes_action": "replace", "notes": "Should not be saved",
                })
                self.assertEqual(response.status_code, 200)
                self.assertIn("status", response.context["form"].errors)
                for mouse in (self.mouse_1, self.mouse_2):
                    mouse.refresh_from_db()
                    self.assertEqual(mouse.status, "Alive")
                    self.assertNotEqual(mouse.notes, "Should not be saved")

    def test_notes_only_edit_preserves_current_and_legacy_status(self):
        for status in ("Culled", "Archived"):
            with self.subTest(status=status):
                self.mouse_1.status = status
                self.mouse_1.save(update_fields=["status"])
                response = self.client.post(self.edit_selected_url, {
                    "apply_bulk_edit": "1", "selected_mouse": [self.mouse_1.pk],
                    "notes_action": "replace", "notes": "Updated notes",
                })
                self.assertEqual(response.status_code, 302)
                self.mouse_1.refresh_from_db()
                self.assertEqual(self.mouse_1.notes, "Updated notes")
                self.assertEqual(self.mouse_1.status, status)

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
            "Remove existing procedures before moving these animals to a protocol "
            "that does not allow regulated procedures: M1",
        )

        self.mouse_1.refresh_from_db()
        self.mouse_2.refresh_from_db()
        self.assertEqual(self.mouse_1.protocol, self.protocol)
        self.assertEqual(self.mouse_2.protocol, self.protocol)

    def test_single_edit_does_not_render_mouse_id_input(self):
        response = self.client.get(
            reverse("lab:edit_selected_mice_all") + "?" + urlencode({"selected_mouse": self.mouse_1.pk}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Animal ID")
        self.assertContains(response, self.mouse_1.mouse_id)
        self.assertContains(response, "Local identifier")
        self.assertNotContains(response, 'name="mouse_id"')

    def test_single_mouse_edit_links_to_service_recording_with_mouse_selected(self):
        edit_url = reverse("lab:edit_selected_mice_all") + "?" + urlencode({"selected_mouse": self.mouse_1.pk})
        response = self.client.get(
            edit_url,
            {
                "return_url": self.return_url, "selected_mouse": [self.mouse_1.pk],
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

        self.assertEqual(return_parts.path, reverse("lab:edit_selected_mice_all"))
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
            reverse("lab:edit_selected_mice_all") + "?" + urlencode({"selected_mouse": self.mouse_1.pk}),
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
            reverse("lab:edit_selected_mice_all") + "?" + urlencode({"selected_mouse": self.mouse_1.pk}),
            {
                "apply_bulk_edit": "1",
                "update_status": "on", "update_severity": "on", "update_protocol": "on",
                "notes_action": "replace",
                "return_url": self.return_url,
                "selected_mouse": [self.mouse_1.pk],
                "date_of_birth": "",
                "tattoo": "",
                "genotype": "Gad2",
                "crossing": "",
                "crossing_type": "",
                f"{individual_mouse_field_prefix(self.mouse_1)}_sex": "F",
                "status": "Culled",
                "protocol_start_date": "",
                "cull_date": "",
                "protocol": self.protocol.pk,
                "severity": "Mild",
                "notes": "Updated mouse note",
                "update_crossing": "on",
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

    def test_single_mouse_genotypes_live_in_embedded_individual_row(self):
        crossing, lines = create_test_crossing(("MutA", "MutB"))
        sync_mouse_structured_genetics(self.mouse_1, crossing, {lines[0].pk: "het", lines[1].pk: "hom"})
        response = self.client.get(reverse("lab:edit_selected_mice_all"), {"selected_mouse": self.mouse_1.pk})
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        stem = individual_mouse_field_prefix(self.mouse_1)
        self.assertEqual(form[f"{stem}_genotype_line_{lines[0].pk}"].value(), "het")
        self.assertEqual(form[f"{stem}_genotype_line_{lines[1].pk}"].value(), "hom")
        self.assertNotContains(response, 'name="genotype_line_')


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
            reverse("lab:edit_selected_mice_all") + "?" + urlencode({"selected_mouse": self.mouse_1.pk}),
            {
                "apply_bulk_edit": "1",
                "update_status": "on", "update_severity": "on", "update_protocol": "on",
                "notes_action": "replace",
                "return_url": self.return_url,
                "selected_mouse": [self.mouse_1.pk],
                "date_of_birth": "",
                "tattoo": "",
                "breeding_pair": "",
                f"{individual_mouse_field_prefix(self.mouse_1)}_sex": "F",
                "status": "Alive",
                "protocol_start_date": "",
                "cull_date": "",
                "protocol": self.protocol.pk,
                "severity": "",
                "notes": "Changed crossing",
                "update_crossing": "on",
                "crossing_definition": str(new_crossing.pk),
                "crossing_search": f"Mouse - {new_crossing.display_name}",
                f"{individual_mouse_field_prefix(self.mouse_1)}_genotype_line_{new_lines_by_name['MutC'].pk}": "wt",
                f"{individual_mouse_field_prefix(self.mouse_1)}_genotype_line_{new_lines_by_name['MutB'].pk}": "hom",
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
        edit_url = reverse("lab:edit_selected_mice_all") + "?" + urlencode({"selected_mouse": legacy_mouse.pk})

        response = self.client.get(edit_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Legacy line")
        self.assertContains(response, "Legacy line")
        self.assertContains(response, "Legacy genotype")

        response = self.client.post(
            edit_url,
            {
                "apply_bulk_edit": "1",
                "update_status": "on", "update_severity": "on", "update_protocol": "on",
                "notes_action": "replace",
                "return_url": self.return_url,
                "selected_mouse": [legacy_mouse.pk],
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
            reverse("lab:edit_selected_mice_all") + "?" + urlencode({"selected_mouse": self.mouse_1.pk}),
            {
                "apply_bulk_edit": "1",
                "update_status": "on", "update_severity": "on", "update_protocol": "on",
                "notes_action": "replace",
                "return_url": self.return_url,
                "selected_mouse": [self.mouse_1.pk],
                "date_of_birth": "",
                "tattoo": "",
                "breeding_pair": "",
                "genotype": "Gad2",
                "crossing": "",
                "crossing_type": "",
                f"{individual_mouse_field_prefix(self.mouse_1)}_sex": "F",
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
                "Remove existing procedures before moving these animals to a "
                "protocol that does not allow regulated procedures: M1"
            ),
            response.context["form"].non_field_errors(),
        )

        self.mouse_1.refresh_from_db()
        self.assertEqual(self.mouse_1.protocol, self.protocol)

    def test_single_mouse_edit_creates_missing_procedure(self):
        response = self.client.post(
            reverse("lab:edit_selected_mice_all") + "?" + urlencode({"selected_mouse": self.mouse_1.pk}),
            {
                "create_procedures": "1",
                "return_url": self.return_url,
                "selected_mouse": [self.mouse_1.pk],
                "new_procedure-procedure_type": self.procedure_type.pk,
                "new_procedure-date": "2026-07-20",
                "new_procedure-lab_member": "AB",
                "new_procedure-lab_book_page": "42",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
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
            reverse("lab:edit_selected_mice_all") + "?" + urlencode({"selected_mouse": self.mouse_1.pk}),
            {
                "create_procedures": "1",
                "return_url": self.return_url,
                "selected_mouse": [self.mouse_1.pk],
                "new_procedure-procedure_type": self.procedure_type.pk,
                "new_procedure-date": "2026-07-21",
                "new_procedure-lab_member": "CD",
                "new_procedure-lab_book_page": "99",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
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
            reverse("lab:edit_selected_mice_all") + "?" + urlencode({"selected_mouse": self.mouse_1.pk}),
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
