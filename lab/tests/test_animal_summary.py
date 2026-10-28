from datetime import date
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from ..forms import individual_mouse_field_prefix
from ..models import Experiment, GenotypeCategory, ImageStorageLocation, Mouse, MouseGenotype, Procedure, ProcedureType, Project, Protocol, RecordingImage, RecordingType
from ..test_navigation import Markup
from ..views.common import url_with_query_params
from ._support import AuthenticatedTestCase, create_recording, create_test_crossing


@override_settings(ALLOWED_HOSTS=["testserver"])
class AnimalSummaryTests(AuthenticatedTestCase):
    def setUp(self):
        self.protocol = Protocol.objects.create(protocol_number=81, licence_reference="SUMMARY", name="Summary protocol")
        self.crossing, self.lines = create_test_crossing(("Summary gene", "Other gene"))
        self.mice = [Mouse.objects.create(
            mouse_id=f"Summary-{index}", protocol=self.protocol,
            date_of_birth=date(2026, 1, 2), crossing_definition=self.crossing,
            sex="F" if index == 0 else "M", tattoo=f"Local-{index}",
            notes="First animal notes" if index == 0 else "",
            added_by=self.user,
        ) for index in range(2)]
        self.other_mouse = Mouse.objects.create(mouse_id="Unrelated animal", protocol=self.protocol)
        self.kind = RecordingType.objects.get(slug="imaging")
        self.project = Project.objects.create(name="Summary project")
        self.experiment = Experiment.objects.create(project=self.project, name="Summary experiment", recording_type=self.kind)
        self.recording = create_recording(
            experiment=self.experiment, recording_type=self.kind, owner=self.user,
            recording_date=date(2026, 3, 4), mice=self.mice,
        )
        self.url = reverse("lab:recording_animals", args=[self.project.pk, self.experiment.pk, self.recording.pk])
        self.list_url = reverse("lab:recordings_page", args=[self.project.pk, self.experiment.pk])
        self.list_state = self.list_url + "?sort=recording_date&direction=asc&per_page=50&page=2"
        self.edit_url = reverse("lab:edit_selected_mice_all")
        procedure_type = ProcedureType.objects.create(name="Summary procedure")
        self.procedure = Procedure.objects.create(
            mouse=self.mice[0], procedure_type=procedure_type, date=date(2026, 2, 3),
            lab_member="Scientist", lab_book_page="Page 15", notes="Procedure notes",
        )
        Procedure.objects.create(mouse=self.other_mouse, procedure_type=procedure_type, lab_member="Unrelated scientist")

    def summary(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        return response, Markup(response.content.decode())

    def test_summary_uses_familiar_tabs_and_has_no_editable_fields(self):
        response, markup = self.summary()
        self.assertEqual([node.attrs["data-mouse-tab"] for node in markup.find("button", role="tab")],
                         ["individual", "shared", "family", "procedures", "services"])
        edit = markup.find("a", **{"data-mouse-summary-edit": None})[0]
        tablist = markup.find("div", role="tablist")[0]
        self.assertIs(edit.parent, tablist.parent)
        self.assertIs([node for node in markup.elements if node.parent is edit.parent][-1], edit)
        self.assertFalse(edit.within("div", role="tablist"))
        self.assertIn("button-primary", edit.attrs["class"])
        content = [node for node in markup.elements if node.within("div", **{"data-mouse-editor": None})]
        self.assertFalse([node for node in content if node.tag in {"form", "input", "select", "textarea"}])
        self.assertContains(response, "Origin")
        self.assertContains(response, "Animal status")
        self.assertContains(response, "Protocol")
        self.assertNotContains(response, "Unrelated animal")
        self.assertNotContains(response, "Save changes")

    def test_animal_name_and_single_selection_use_the_same_summary(self):
        mouse = self.mice[0]
        view_url = reverse("lab:view_selected_mice_licence_protocol", args=[
            self.protocol.licence_reference, self.protocol.protocol_number,
        ])
        direct_url = reverse("lab:animal_summary", args=[mouse.pk])
        params = {"return_url": self.list_state, "mouse_editor_tab": "services"}
        direct = self.client.get(direct_url, params)
        selected = self.client.get(view_url, {**params, "selected_mouse": mouse.pk})
        for response in (direct, selected):
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "lab/animal_summary.html")
            self.assertTrue(response.context["single_mouse_mode"])
            self.assertEqual(response.context["selected_mice"], [mouse])
            self.assertEqual(response.context["active_tab"], "services")
            self.assertNotContains(response, "General information")
        direct_markup, selected_markup = [Markup(response.content.decode()) for response in (direct, selected)]
        for key in ("shared", "procedures", "services"):
            direct_panel = direct_markup.find("section", **{"data-mouse-tab-panel": key})[0]
            selected_panel = selected_markup.find("section", **{"data-mouse-tab-panel": key})[0]
            self.assertEqual(direct_panel.text, selected_panel.text)
        direct_edit = direct_markup.find("a", **{"data-mouse-summary-edit": None})[0].attrs["href"]
        selected_edit = selected_markup.find("a", **{"data-mouse-summary-edit": None})[0].attrs["href"]
        self.assertEqual(urlsplit(direct_edit).path, urlsplit(selected_edit).path)
        self.assertEqual(parse_qs(urlsplit(direct_edit).query)["selected_mouse"], [mouse.pk])

    def test_single_animal_summary_rejects_missing_animals_and_requires_login(self):
        url = reverse("lab:animal_summary", args=[self.mice[0].pk])
        self.assertEqual(self.client.get(reverse("lab:animal_summary", args=["Missing animal"])).status_code, 404)
        self.assertEqual(self.client.post(url, {"status": "Culled"}).status_code, 405)
        self.mice[0].refresh_from_db()
        self.assertEqual(self.mice[0].status, "Alive")
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_renaming_from_single_animal_summary_returns_to_the_new_animal_url(self):
        mouse = self.mice[0]
        url = reverse("lab:animal_summary", args=[mouse.pk])
        response = self.client.get(url, {"return_url": self.list_state})
        edit_link = Markup(response.content.decode()).find("a", **{"data-mouse-summary-edit": None})[0].attrs["href"]
        summary_return = parse_qs(urlsplit(edit_link).query)["return_url"][0]
        result = self.client.post(urlsplit(edit_link).path, {
            "apply_bulk_edit": "1", "selected_mouse": [mouse.pk],
            "return_url": summary_return, "confirm_mouse_ids": "on",
            individual_mouse_field_prefix(mouse) + "_mouse_id": "Renamed single animal",
        })
        self.assertEqual(result.status_code, 302)
        new_summary_url = parse_qs(urlsplit(result.url).query)["return_url"][0]
        self.assertEqual(urlsplit(new_summary_url).path, reverse("lab:animal_summary", args=["Renamed single animal"]))
        self.assertEqual(parse_qs(urlsplit(new_summary_url).query)["return_url"], [self.list_state])
        self.assertContains(self.client.get(new_summary_url), "Renamed single animal")

    def test_shared_values_and_per_animal_differences_include_blank_values(self):
        response, markup = self.summary()
        self.assertContains(response, "02/01/2026")
        self.assertContains(response, "Alive")
        self.assertContains(response, "Varies between animals")
        differences = [node for node in markup.find("details") if "animal-summary-differences" in node.attrs.get("class", "")]
        self.assertEqual(len(differences), 1)  # Only notes differ; both animals have unknown genotype results.
        notes = next(node for node in differences if "First animal notes" in node.text)
        self.assertIn(self.mice[0].pk, notes.text)
        self.assertIn(self.mice[1].pk, notes.text)
        self.assertIn("—", notes.text)

    def test_genotype_results_show_uncertainty_unknown_and_retired_labels(self):
        category = GenotypeCategory.objects.create(code="retired", label="Historical result", active=False)
        MouseGenotype.objects.create(mouse=self.mice[0], mouse_line=self.lines[0], zygosity=category.code, is_uncertain=True)
        response, markup = self.summary()
        individual = next(node for node in markup.find("section") if node.attrs.get("id") == "mouse-panel-individual")
        self.assertIn("Historical result", individual.text)
        self.assertIn("Uncertain", individual.text)
        self.assertIn("Unknown", individual.text)
        self.assertIn("Local-0", individual.text)
        self.assertIn("Female", individual.text)
        self.assertContains(response, self.crossing.display_name)
        self.assert_contextual_link(
            response, reverse("lab:animal_summary", args=[self.mice[0].pk]),
            url_with_query_params(self.url, return_url=self.list_url, mouse_editor_tab="individual"),
        )

    def test_genotypes_use_shared_column_headings_in_summary_and_editor(self):
        for url, params in (
            (self.url, {}),
            (self.edit_url, {"selected_mouse": [mouse.pk for mouse in self.mice]}),
        ):
            with self.subTest(url=url):
                response = self.client.get(url, params)
                self.assertEqual(response.status_code, 200)
                markup = Markup(response.content.decode())
                headings = [node.text.strip() for node in markup.find("th", scope="col")
                            if node.within("section", id="mouse-panel-individual")]
                self.assertEqual(headings, ["Animal ID", "Local identifier", "Sex", *[line.name for line in self.lines]])
                for node in markup.find("label"):
                    if node.text.strip() in [line.name for line in self.lines] and node.within("section", id="mouse-panel-individual"):
                        self.assertTrue(node.within("span", **{"class": "visually-hidden"}))
        _, markup = self.summary()
        shared = markup.find("section", id="mouse-panel-shared")[0]
        self.assertNotIn("Genotype", shared.text)

    def test_historical_calls_align_with_their_own_column(self):
        _, historical_lines = create_test_crossing(("Historical gene",))
        MouseGenotype.objects.create(mouse=self.mice[1], mouse_line=historical_lines[0], zygosity="het")
        _, markup = self.summary()
        headings = [node.text.strip() for node in markup.find("th", scope="col")
                    if node.within("section", id="mouse-panel-individual")]
        self.assertEqual(headings[-3:], [line.name for line in self.lines] + ["Historical gene"])
        rows = [node for node in markup.find("tr") if node.within("tbody")
                and node.within("section", id="mouse-panel-individual")]
        for index, row in enumerate(rows):
            cells = [node for node in markup.find("td") if node.parent is row]
            self.assertEqual(len(cells), 5)
            self.assertEqual(cells[-1].text.strip(), "—" if index == 0 else "Het")

    def test_legacy_genotypes_and_different_crossings_are_retained(self):
        self.mice[1].crossing_definition = None
        self.mice[1].crossing = "Historical crossing"
        self.mice[1].genotype = "Historical genotype"
        self.mice[1].save()
        response, _ = self.summary()
        self.assertContains(response, "Historical crossing")
        self.assertContains(response, "Historical genotype")
        self.assertEqual(len({row["crossing"] for row in response.context["individual_rows"]}), 2)

    def test_procedures_only_include_linked_animals_and_preserve_notes(self):
        response, markup = self.summary(mouse_editor_tab="procedures")
        panel = next(node for node in markup.find("section") if node.attrs.get("id") == "mouse-panel-procedures")
        for text in (self.mice[0].pk, "Summary procedure", "03/02/2026", "Scientist", "Page 15", "Procedure notes"):
            self.assertIn(text, panel.text)
        self.assertNotContains(response, "Unrelated scientist")
        self.assertEqual(list(response.context["procedures"]), [self.procedure])

    def test_edit_selection_active_tab_and_return_chain_are_preserved(self):
        response, markup = self.summary(return_url=self.list_state, mouse_editor_tab="procedures")
        edit_link = markup.find("a", **{"data-mouse-summary-edit": None})[0].attrs["href"]
        edit_query = parse_qs(urlsplit(edit_link).query)
        self.assertEqual(edit_query["selected_mouse"], [mouse.pk for mouse in self.mice])
        self.assertEqual(edit_query["mouse_editor_tab"], ["individual"])
        summary_return = edit_query["return_url"][0]
        self.assertEqual(parse_qs(urlsplit(summary_return).query), {
            "return_url": [self.list_state], "mouse_editor_tab": ["procedures"],
        })
        self.assertEqual(markup.find("a", **{"class": "back-link"})[0].attrs["href"], self.list_state)
        editor = self.client.get(edit_link)
        self.assertEqual(editor.status_code, 200)
        editor_markup = Markup(editor.content.decode())
        self.assertEqual(editor_markup.find("input", name="mouse_editor_tab")[0].attrs["value"], "individual")
        result = self.client.post(self.edit_url, {
            "apply_bulk_edit": "1", "selected_mouse": [mouse.pk for mouse in self.mice],
            "return_url": summary_return, "mouse_editor_tab": "procedures",
            "update_status": "on", "status": "Culled",
        })
        self.assertRedirects(result, summary_return)
        self.assertEqual(list(Mouse.objects.filter(pk__in=[mouse.pk for mouse in self.mice]).values_list("status", flat=True)), ["Culled", "Culled"])

    def test_renaming_in_editor_keeps_summary_link_valid(self):
        response, markup = self.summary(mouse_editor_tab="individual")
        summary_return = parse_qs(urlsplit(markup.find("a", **{"data-mouse-summary-edit": None})[0].attrs["href"]).query)["return_url"][0]
        result = self.client.post(self.edit_url, {
            "apply_bulk_edit": "1", "selected_mouse": [mouse.pk for mouse in self.mice],
            "return_url": summary_return, "mouse_editor_tab": "individual", "confirm_mouse_ids": "on",
            individual_mouse_field_prefix(self.mice[0]) + "_mouse_id": "Renamed summary animal",
        })
        self.assertEqual(result.status_code, 302)
        self.assertEqual(parse_qs(urlsplit(result.url).query)["mouse_editor_tab"], ["individual"])
        returned_summary = self.client.get(summary_return)
        self.assertContains(returned_summary, "Renamed summary animal")
        self.assertNotContains(returned_summary, ">Summary-0<")

    def test_single_animal_is_embedded_in_details_and_unknown_tabs_fall_back(self):
        self.recording.mice.set(self.mice[:1])
        for tab in ("individual", "ids", "invalid"):
            with self.subTest(tab=tab):
                response, markup = self.summary(mouse_editor_tab=tab)
                self.assertEqual([node.attrs["data-mouse-tab"] for node in markup.find("button", role="tab")], ["shared", "family", "procedures", "services"])
                self.assertEqual(response.context["active_tab"], "shared")
                local_value = next(node for node in markup.find("td") if "Local-0" in node.text)
                self.assertTrue(local_value.within("section", id="mouse-panel-shared"))

    def test_empty_recording_and_no_procedures_have_clear_empty_states(self):
        Procedure.objects.filter(mouse__in=self.mice).delete()
        response, _ = self.summary()
        self.assertContains(response, "No existing procedures belong to the selected animals.")
        self.recording.mice.clear()
        response, markup = self.summary()
        self.assertContains(response, "No animals are linked to this recording.")
        self.assertFalse(markup.find("a", **{"data-mouse-summary-edit": None}))
        self.assertFalse(markup.find("div", role="tablist"))

    def test_return_url_is_sanitized_and_summary_cannot_be_posted(self):
        response, markup = self.summary(return_url="https://example.com/unsafe")
        self.assertEqual(response.context["return_url"], self.list_url)
        self.assertNotContains(response, "https://example.com/unsafe")
        result = self.client.post(self.url, {"update_status": "on", "status": "Culled"})
        self.assertEqual(result.status_code, 405)
        self.mice[0].refresh_from_db()
        self.assertEqual(self.mice[0].status, "Alive")

    def test_authentication_scope_and_viewing_another_owners_recording(self):
        other_user = get_user_model().objects.create_user(username="summary-viewer")
        self.client.force_login(other_user)
        response = self.client.get(self.list_url)
        self.assertContains(response, "View all")
        self.assert_contextual_link(response, self.url, self.list_url)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.get(reverse("lab:recording_animals", args=[self.project.pk, self.experiment.pk + 1000, self.recording.pk])).status_code, 404)
        different_experiment = Experiment.objects.create(project=self.project, name="Other experiment", recording_type=self.kind)
        self.assertEqual(self.client.get(reverse("lab:recording_animals", args=[self.project.pk, different_experiment.pk, self.recording.pk])).status_code, 404)
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.url).path, reverse("lab:login"))

    def test_recording_button_preserves_list_filters_and_pagination(self):
        response = self.client.get(self.list_state)
        self.assertContains(response, "View all")
        self.assert_contextual_link(response, self.url, self.list_state)
        markup = Markup(response.content.decode())
        view_all = next(node for node in markup.find("a") if node.text.strip() == "View all")
        self.assertTrue(view_all.within("td", **{"class": "recording-animals-cell"}))
        self.assertFalse(view_all.within("details"))
        self.assertIn("2 animals", view_all.parent.text)
        self.assertNotContains(response, "View animals")

    def test_single_recording_animal_uses_name_link_without_group_link(self):
        self.recording.mice.set(self.mice[:1])
        response = self.client.get(self.list_url)
        markup = Markup(response.content.decode())
        cell = markup.find("td", **{"class": "recording-animals-cell"})[0]
        self.assertIn(self.mice[0].pk, cell.text)
        self.assertFalse(markup.find("details", **{"class": "recording-animals"}))
        self.assertNotContains(response, "View all")
        self.assertNotContains(response, "View animals")
        self.assertContains(response, reverse("lab:animal_summary", args=[self.mice[0].pk]))

    def test_summary_escapes_notes_and_query_count_does_not_grow_per_animal(self):
        self.mice[0].notes = "<script>alert('notes')</script>\nSecond line"
        self.mice[0].save()
        with CaptureQueriesContext(connection) as small:
            response, _ = self.summary()
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert(")
        for index in range(10):
            self.recording.mice.add(Mouse.objects.create(
                mouse_id=f"Extra-summary-{index}", protocol=self.protocol, crossing_definition=self.crossing,
            ))
        with CaptureQueriesContext(connection) as large:
            self.summary()
        self.assertEqual(len(small), len(large))

    def genotyping_recording(self, mice, notes="Selected genotyping record"):
        experiment = Experiment.objects.get(project__is_service=True, recording_type__slug="genotyping")
        return create_recording(
            experiment=experiment, recording_type=experiment.recording_type,
            owner=self.user, recording_date=date(2026, 4, 5), mice=mice,
            values={"lab_member": "Genotyping scientist"}, data_paths=["/genotyping/gel.png"], notes=notes,
        )

    def services_panel(self, response):
        markup = Markup(response.content.decode())
        panel = markup.find("section", id="mouse-panel-services")[0]
        tab = markup.find("button", **{"data-mouse-tab": "services"})[0]
        self.assertIn("Services & recordings", tab.text)
        return markup, panel

    def test_services_lists_all_linked_recording_types_once_and_excludes_unrelated_records(self):
        self.genotyping_recording(self.mice)
        self.genotyping_recording(self.mice[:1], notes="Single animal genotyping")
        self.genotyping_recording([self.other_mouse], notes="Unrelated genotyping record")
        response, _ = self.summary(mouse_editor_tab="services")
        _, panel = self.services_panel(response)
        for text in ("Imaging", "Genotyping", "Genotyping scientist", "/genotyping/gel.png", "Animals", "Notes"):
            self.assertIn(text, panel.text)
        self.assertEqual(panel.text.count("Selected genotyping record"), 1)
        self.assertEqual(panel.text.count("Single animal genotyping"), 1)
        self.assertNotIn("Unrelated genotyping record", panel.text)
        self.assertEqual(sum(len(section["table"]["rows"]) for section in response.context["animal_recording_sections"]), 3)

    def test_services_recording_actions_date_and_animal_navigation(self):
        self.genotyping_recording(self.mice[:1])
        image = RecordingImage.objects.create(recording=self.recording,
            storage_location=ImageStorageLocation.objects.create(directory='/unavailable-summary-images'),
            filename='missing.webp', original_name='Missing image', width=10, height=10, size_bytes=20)
        selection = {'selected_mouse': [mouse.pk for mouse in self.mice], 'mouse_editor_tab': 'services'}
        for url, params in ((self.url, {'mouse_editor_tab': 'services'}), (self.edit_url, selection)):
            with self.subTest(url=url), patch('lab.recording_images.image_path', side_effect=AssertionError('Table touched image storage')):
                response = self.client.get(url, params)
            self.assertEqual(response.status_code, 200)
            markup, panel = self.services_panel(response)
            tables = [node for node in markup.find('table') if node.within('section', id='mouse-panel-services')]
            for table in tables:
                headings = [node.text.strip() for node in markup.find('th', scope='col') if node.within('table') and node.parent.parent.parent is table]
                self.assertEqual(headings[-1], 'Actions')
            date_cells = [node for node in markup.find('td', **{'class': 'recording-date-cell'}) if node.within('section', id='mouse-panel-services')]
            self.assertTrue(date_cells)
            self.assertFalse([link for link in markup.find('a') if link.within('td', **{'class': 'recording-date-cell'})])
            actions = [node for node in markup.find('a') if node.text.strip() == 'See in Experiment' and node.within('section', id='mouse-panel-services')]
            self.assertEqual(len(actions), 2)
            selected = next(node for node in actions if parse_qs(urlsplit(node.attrs['href']).query)['selected_recording'] == [str(self.recording.pk)])
            self.assertTrue(selected.within('td', **{'class': 'action-cell'}))
            self.assertEqual(urlsplit(selected.attrs['href']).fragment, f'recording-{self.recording.pk}')
            self.assertEqual(parse_qs(urlsplit(selected.attrs['href']).query)['return_url'], [response.wsgi_request.get_full_path()])
            images = next(node for node in markup.find('a') if node.text.strip() == 'Images (1)')
            self.assertTrue(images.within('td', **{'class': 'action-cell'}))
            self.assertIn('data-recording-gallery', images.attrs)
            self.assertEqual(urlsplit(images.attrs['href']).path, reverse('lab:recording_images', args=[self.project.pk, self.experiment.pk, self.recording.pk]))
            self.assertEqual(len(markup.find('dialog', **{'data-image-dialog': None})), 1)
            view_all = [node for node in markup.find('a') if node.text.strip() == 'View all' and node.within('td', **{'class': 'recording-animals-cell'})]
            self.assertEqual(len(view_all), 1)  # No redundant group link for the single-animal record.
            self.assertEqual(urlsplit(view_all[0].attrs['href']).path, self.url)
            self.assertEqual(parse_qs(urlsplit(view_all[0].attrs['href']).query)['return_url'], [response.wsgi_request.get_full_path()])

    def test_both_edit_routes_show_the_same_recordings_and_keep_service_creation(self):
        self.genotyping_recording(self.mice)
        for url in (
            self.edit_url,
            reverse("lab:edit_selected_mice_licence_protocol", args=[self.protocol.licence_reference, self.protocol.protocol_number]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url, {"selected_mouse": [mouse.pk for mouse in self.mice], "mouse_editor_tab": "services"})
                self.assertEqual(response.status_code, 200)
                markup, panel = self.services_panel(response)
                self.assertEqual(panel.text.count("Selected genotyping record"), 1)
                self.assertIn("Add record", panel.text)
                add_links = [node for node in markup.find("a") if node.text.strip() == "Add record"]
                self.assertTrue(add_links)
                for link in add_links:
                    query = parse_qs(urlsplit(link.attrs["href"]).query)
                    self.assertEqual(query["mice"], [",".join(mouse.pk for mouse in self.mice)])
                    return_query = parse_qs(urlsplit(query["return_url"][0]).query)
                    self.assertEqual(return_query["mouse_editor_tab"], ["services"])
                    self.assertEqual(return_query["selected_mouse"], [mouse.pk for mouse in self.mice])

    def test_summary_services_are_read_only_and_edit_opens_the_corresponding_tab(self):
        self.genotyping_recording(self.mice)
        response, markup = self.summary(mouse_editor_tab="services", return_url=self.list_state)
        _, panel = self.services_panel(response)
        self.assertNotIn("Add record", panel.text)
        self.assertNotIn("Delete", panel.text)
        self.assertFalse([node for node in markup.elements if node.tag in {"input", "select", "textarea", "form"} and node.within("section", id="mouse-panel-services")])
        self.assertEqual(response.context["active_tab"], "services")
        edit_link = markup.find("a", **{"data-mouse-summary-edit": None})[0].attrs["href"]
        query = parse_qs(urlsplit(edit_link).query)
        self.assertEqual(query["mouse_editor_tab"], ["individual"])
        self.assertEqual(parse_qs(urlsplit(query["return_url"][0]).query)["mouse_editor_tab"], ["services"])
        editor = self.client.get(edit_link)
        _, editor_panel = self.services_panel(editor)
        self.assertIn("Add record", editor_panel.text)
        self.assertIn("Selected genotyping record", editor_panel.text)

    def test_historical_recordings_remain_visible_after_type_and_experiment_are_archived(self):
        record = self.genotyping_recording(self.mice)
        record.recording_type.active = False
        record.recording_type.save()
        record.experiment.archived = True
        record.experiment.save()
        response, _ = self.summary()
        _, panel = self.services_panel(response)
        self.assertIn("Selected genotyping record", panel.text)

    def test_recording_list_has_a_clear_empty_state_but_service_creation_remains_available(self):
        self.recording.mice.clear()
        response = self.client.get(self.edit_url, {"selected_mouse": self.mice[0].pk, "mouse_editor_tab": "services"})
        _, panel = self.services_panel(response)
        self.assertIn("No service records or recordings are linked to the selected animals.", panel.text)
        self.assertIn("Add record", panel.text)

    def test_procedure_selection_opens_read_only_summary_then_scoped_editor(self):
        view_url = reverse("lab:view_selected_mice_licence_protocol", args=[self.protocol.licence_reference, self.protocol.protocol_number])
        procedure_url = reverse("lab:procedure_page_licence_protocol", args=[self.protocol.licence_reference, self.protocol.protocol_number])
        list_state = procedure_url + "?show_current_year=0&sex=F&sort=mouse_id&direction=asc&page=2"
        selection = [mouse.pk for mouse in self.mice]
        response = self.client.get(view_url, {"selected_mouse": selection, "return_url": list_state, "mouse_editor_tab": "services"})
        self.assertEqual(response.status_code, 200)
        markup = Markup(response.content.decode())
        self.assertIn(self.protocol.name, markup.find("div", **{"class": "page-subtitle"})[0].text)
        self.assertNotContains(response, "Undated recording")
        self.assertEqual(markup.find("a", **{"class": "back-link"})[0].attrs["href"], list_state)
        self.assertTrue(any(link.attrs.get("aria-current") == "true" and link.text.strip() == "Procedures" for link in markup.find("a")))
        edit_link = markup.find("a", **{"data-mouse-summary-edit": None})[0].attrs["href"]
        expected_edit_url = reverse("lab:edit_selected_mice_licence_protocol", args=[self.protocol.licence_reference, self.protocol.protocol_number])
        self.assertEqual(urlsplit(edit_link).path, expected_edit_url)
        query = parse_qs(urlsplit(edit_link).query)
        self.assertEqual(query["selected_mouse"], selection)
        self.assertEqual(query["mouse_editor_tab"], ["individual"])
        summary_return = query["return_url"][0]
        self.assertEqual(parse_qs(urlsplit(summary_return).query)["return_url"], [list_state])
        self.assertEqual(parse_qs(urlsplit(summary_return).query)["selected_mouse"], selection)
        editor = self.client.get(edit_link)
        self.assertEqual(editor.status_code, 200)
        self.assertContains(editor, "Add record")
        saved = self.client.post(expected_edit_url, {
            "selected_mouse": selection, "return_url": summary_return,
            "apply_bulk_edit": "1", "update_status": "on", "status": "Culled", "mouse_editor_tab": "services",
        })
        self.assertRedirects(saved, summary_return)

    def test_procedure_view_selection_post_only_redirects_and_never_saves_edits(self):
        view_url = reverse("lab:view_selected_mice_licence_protocol", args=[self.protocol.licence_reference, self.protocol.protocol_number])
        response = self.client.post(view_url, {
            "selected_mouse": [self.mice[0].pk, self.mice[0].pk], "update_status": "on", "status": "Culled",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(parse_qs(urlsplit(response.url).query)["selected_mouse"], [self.mice[0].pk])
        summary = self.client.get(response.url)
        self.assertEqual(summary.status_code, 200)
        self.assertTrue(summary.context["single_mouse_mode"])
        self.mice[0].refresh_from_db()
        self.assertEqual(self.mice[0].status, "Alive")
        self.assertEqual(self.client.delete(view_url).status_code, 405)

    def test_procedure_summary_rejects_missing_and_out_of_protocol_animals(self):
        view_url = reverse("lab:view_selected_mice_licence_protocol", args=[self.protocol.licence_reference, self.protocol.protocol_number])
        fallback = reverse("lab:procedure_page_licence_protocol", args=[self.protocol.licence_reference, self.protocol.protocol_number])
        other_protocol = Protocol.objects.create(licence_reference="OTHER", protocol_number=82, name="Other protocol")
        self.other_mouse.protocol = other_protocol
        self.other_mouse.save()
        for selection in ([], ["Missing animal"], [self.mice[0].pk, self.other_mouse.pk]):
            with self.subTest(selection=selection):
                response = self.client.get(view_url, {"selected_mouse": selection, "return_url": "https://example.com/unsafe"})
                self.assertRedirects(response, fallback)
        self.client.logout()
        self.assertEqual(self.client.get(view_url, {"selected_mouse": self.mice[0].pk}).status_code, 302)

    def test_renaming_from_procedure_summary_updates_its_selection_for_returning(self):
        view_url = reverse("lab:view_selected_mice_licence_protocol", args=[self.protocol.licence_reference, self.protocol.protocol_number])
        response = self.client.get(view_url, {"selected_mouse": [mouse.pk for mouse in self.mice]})
        edit_link = Markup(response.content.decode()).find("a", **{"data-mouse-summary-edit": None})[0].attrs["href"]
        query = parse_qs(urlsplit(edit_link).query)
        result = self.client.post(urlsplit(edit_link).path, {
            "apply_bulk_edit": "1", "selected_mouse": [mouse.pk for mouse in self.mice],
            "return_url": query["return_url"][0], "confirm_mouse_ids": "on",
            individual_mouse_field_prefix(self.mice[0]) + "_mouse_id": "Renamed procedure animal",
        })
        self.assertEqual(result.status_code, 302)
        summary_return = parse_qs(urlsplit(result.url).query)["return_url"][0]
        self.assertEqual(parse_qs(urlsplit(summary_return).query)["selected_mouse"], ["Renamed procedure animal", self.mice[1].pk])
        self.assertContains(self.client.get(summary_return), "Renamed procedure animal")

    def test_changing_protocol_in_editor_keeps_the_summary_accessible(self):
        view_url = reverse("lab:view_selected_mice_licence_protocol", args=[self.protocol.licence_reference, self.protocol.protocol_number])
        new_protocol = Protocol.objects.create(licence_reference="NEW", protocol_number=83, name="New protocol")
        response = self.client.get(view_url, {"selected_mouse": [mouse.pk for mouse in self.mice]})
        edit_link = Markup(response.content.decode()).find("a", **{"data-mouse-summary-edit": None})[0].attrs["href"]
        result = self.client.post(urlsplit(edit_link).path, {
            "apply_bulk_edit": "1", "selected_mouse": [mouse.pk for mouse in self.mice],
            "return_url": parse_qs(urlsplit(edit_link).query)["return_url"][0],
            "update_protocol": "on", "protocol": new_protocol.pk,
        })
        self.assertEqual(result.status_code, 302)
        self.assertEqual(urlsplit(result.url).path, reverse("lab:view_selected_mice_licence_protocol", args=["NEW", 83]))
        summary = self.client.get(result.url)
        self.assertEqual(summary.status_code, 200)
        self.assertContains(summary, "New protocol")
