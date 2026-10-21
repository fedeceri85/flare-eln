from datetime import date
from urllib.parse import parse_qs, urlsplit
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from ..test_navigation import Markup
from ..models import Experiment, Mouse, Project, Protocol, Recording, RecordingField, RecordingType
from ._support import AuthenticatedTestCase, create_recording, create_test_crossing, crossing_post_data


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
            data_paths=["/data/imaging/newer"],
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
            data_paths=["/data/imaging/older"],
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
        self.assert_contextual_link(
            response, self.recordings_url,
            reverse("lab:experiments_page", args=[self.project.pk]),
        )

    def test_recordings_page_renders_sorted_table_and_actions(self):
        response = self.client.get(self.recordings_url)
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertLess(
            content.index(self.newer_recording.data_paths[0]),
            content.index(self.older_recording.data_paths[0]),
        )

        for label in (
            "Date",
            "Mice",
            "Sequence",
            "Objective",
            "FPS",
            "Frames",
            "Discarded",
            "Recording Paths",
            "Notes",
        ):
            self.assertContains(response, label)

        self.assertNotContains(response, "Recording ID")
        self.assertContains(response, 'href="/"')
        self.assertContains(response, "Back to Experiments")
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
                data_paths=[f"/data/imaging/page-size-{index}"],
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
                self.assertContains(response, expected.data_paths[0])
                self.assertNotContains(response, unexpected.data_paths[0])
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
        self.assertContains(response, self.newer_recording.data_paths[0])
        self.assertNotContains(response, self.older_recording.data_paths[0])
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
        self.assertContains(response, self.older_recording.data_paths[0])
        self.assertNotContains(response, self.newer_recording.data_paths[0])
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
        self.assertNotContains(response, self.newer_recording.data_paths[0])
        self.assertNotContains(response, self.older_recording.data_paths[0])
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
                data_paths=[f"/data/imaging/filler-{index}"],
            )

        response = self.client.get(
            f"{self.recordings_url}?selected_recording={self.older_recording.pk}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["pagination"]["number"], 2)
        self.assertContains(response, self.older_recording.data_paths[0])
        self.assertNotContains(response, self.newer_recording.data_paths[0])
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
        self.assertContains(response, 'name="recording_action"')
        self.assertContains(response, 'value="select_mice"')
        self.assertContains(response, 'formnovalidate')
        self.assertContains(response, "No mice selected")
        paths = Markup(content).find("textarea", name="data_paths")
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].attrs.get("rows"), "2")
        self.assertContains(response, "Recording paths")
        self.assertContains(response, 'name="data_paths"')
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
        self.assertContains(response, "Back to Add recording")
        self.assertContains(response, f'href="{self.add_recording_url}"')
        self.assertContains(response, "Add animals")
        self.assertContains(response, "Add selected mice")
        self.assertContains(response, reverse("lab:add_litter_all"))
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
                "data_paths": "/data/genotyping/inferred-crossing",
                f"value_{crossing_field.pk}": "Wrong crossing",
            },
        )

        self.assertEqual(response.status_code, 302)
        recording = Recording.objects.get(
            data_paths=["/data/genotyping/inferred-crossing"]
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
                "data_paths": "/data/genotyping/mixed-crossing",
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
                data_paths=["/data/genotyping/mixed-crossing"],
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
                "data_paths": "/data/genotyping/missing-crossing",
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
                data_paths=["/data/genotyping/missing-crossing"],
            ).exists()
        )

    def test_add_mouse_from_selector_returns_to_recording_form_with_new_mouse(self):
        crossing, lines = create_test_crossing(("Recording mouse",))
        response = self.client.post(
            reverse("lab:add_litter_all"),
            {
                "return_url": self.add_recording_url,
                "return_with_mouse": "1",
                "mouse_id_prefix": "REC-NEW-MOUSE",
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
                "data_paths": "/data/imaging/added",
                "notes": "added note",
                f"value_{self.objective_field.pk}": "60x",
                f"value_{self.fps_field.pk}": "45.5",
                f"value_{self.frames_field.pk}": "1200",
                f"value_{self.discarded_field.pk}": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.recordings_url)

        recording = Recording.objects.get(data_paths=["/data/imaging/added"])
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

    def test_add_recording_allows_empty_paths(self):
        response = self.client.post(
            self.add_recording_url,
            {
                "return_url": self.recordings_url,
                "mice": self.mouse_1.pk,
                "recording_date": "2026-08-03",
                "sequence_number": "5",
                "data_paths": "",
                "notes": "no path yet",
            },
        )

        self.assertEqual(response.status_code, 302)
        recording = Recording.objects.get(
            experiment=self.experiment,
            recording_date=date(2026, 8, 3),
            sequence_number=5,
        )
        self.assertEqual(recording.data_paths, [])

    def test_add_recording_parses_quoted_windows_paths(self):
        pasted_paths = (
            '  "C:\\recordings\\first folder"  \r\n'
            '\r\n'
            '"\\\\server\\share\\second"\r\n'
            '"\\\\server\\share\\second"'
        )
        response = self.client.post(
            self.add_recording_url,
            {
                "return_url": self.recordings_url,
                "mice": self.mouse_1.pk,
                "recording_date": "2026-08-04",
                "sequence_number": "6",
                "data_paths": pasted_paths,
                "notes": "Windows paste",
            },
        )

        self.assertEqual(response.status_code, 302)
        recording = Recording.objects.get(
            experiment=self.experiment,
            recording_date=date(2026, 8, 4),
            sequence_number=6,
        )
        self.assertEqual(
            recording.data_paths,
            [
                r"C:\recordings\first folder",
                r"\\server\share\second",
                r"\\server\share\second",
            ],
        )

    def test_add_recording_can_attach_multiple_mice(self):
        response = self.client.post(
            self.add_recording_url,
            {
                "return_url": self.recordings_url,
                "mice": f"{self.mouse_1.pk}\n{self.mouse_2.pk}",
                "recording_date": "2026-08-01",
                "sequence_number": "3",
                "data_paths": "/data/genotyping/group-gel",
                "notes": "group gel",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.recordings_url)

        recording = Recording.objects.get(
            data_paths=["/data/genotyping/group-gel"]
        )
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
                "data_paths": "/data/imaging/ambiguous-mouse",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Select registered mouse IDs: REC-PAGE.",
        )
        self.assertFalse(
            Recording.objects.filter(
                data_paths=["/data/imaging/ambiguous-mouse"],
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

    def test_edit_and_duplicate_forms_join_multiple_paths_with_newlines(self):
        self.newer_recording.data_paths = [
            r"C:\recordings\first",
            r"\\server\share\second",
        ]
        self.newer_recording.save(update_fields=["data_paths"])
        expected_initial = (
            "C:\\recordings\\first\n"
            "\\\\server\\share\\second"
        )

        edit_response = self.client.get(self.edit_newer_recording_url)

        duplicate_response = self.client.post(
            self.duplicate_recording_url,
            {
                "return_url": self.recordings_url,
                "selected_recording": [self.newer_recording.pk],
            },
        )
        duplicate_response = self.client.get(duplicate_response["Location"])

        self.assertEqual(
            edit_response.context["form"].fields["data_paths"].initial,
            expected_initial,
        )
        self.assertEqual(
            duplicate_response.context["form"].fields["data_paths"].initial,
            expected_initial,
        )

    def test_recordings_page_renders_paths_on_separate_escaped_lines(self):
        self.newer_recording.data_paths = [
            r"C:\recordings\first",
            "<script>alert('path')</script>",
        ]
        self.newer_recording.save(update_fields=["data_paths"])

        response = self.client.get(self.recordings_url)
        content = response.content.decode()

        self.assertIn(r"C:\recordings\first<br>", content)
        self.assertIn(
            "&lt;script&gt;alert(&#x27;path&#x27;)&lt;/script&gt;",
            content,
        )
        self.assertNotIn("<script>alert('path')</script>", content)

    def test_recordings_page_uses_empty_marker_when_paths_are_absent(self):
        self.newer_recording.data_paths = []
        self.newer_recording.save(update_fields=["data_paths"])

        response = self.client.get(self.recordings_url)
        row = next(
            row
            for row in response.context["table"]["rows"]
            if row["id"] == f"recording-{self.newer_recording.pk}"
        )
        path_cell = next(
            cell
            for cell in row["cells"]
            if cell["class"] == "path-cell"
        )

        self.assertEqual(str(path_cell["value"]), "&mdash;")

    def test_edit_recording_requires_owner_or_staff(self):
        response = self.client.post(
            self.edit_newer_recording_url,
            {
                "return_url": self.recordings_url,
                "mice": self.mouse_2.pk,
                "recording_date": "2026-08-02",
                "sequence_number": "4",
                "data_paths": "/data/imaging/edited",
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
        self.assertEqual(
            self.newer_recording.data_paths,
            ["/data/imaging/edited"],
        )
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
