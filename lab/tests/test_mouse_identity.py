from ..forms import individual_mouse_field_prefix
from datetime import date
from urllib.parse import parse_qs, urlsplit
from django.test import override_settings
from django.urls import reverse
from ..test_navigation import Markup
from ..models import Experiment, Mouse, Procedure, ProcedureType, Project, Protocol, RecordingType
from ._support import AuthenticatedTestCase, create_recording


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
            data_paths=["/data/imaging/session-1"],
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
            data_paths=["/data/abr/session-1"],
            values={
                "click_threshold_db": 35,
            },
        )

        return imaging_recording, abr_recording

    def test_mouse_id_remains_primary_key(self):
        self.assertEqual(Mouse._meta.pk.name, "mouse_id")

    def test_single_mouse_uses_ids_tab_and_shared_delete_confirmation(self):
        response = self.client.get(reverse("lab:edit_mouse", args=[self.mouse.pk]), follow=True)
        self.assertEqual(response.status_code, 200)
        html = Markup(response.content.decode())
        field = html.find("input", name=individual_mouse_field_prefix(self.mouse) + "_mouse_id")[0]
        self.assertTrue(field.within("section", id="mouse-panel-ids"))
        self.assertContains(response, "Confirm Animal ID changes")
        self.assertContains(response, "Delete selected")
        self.assertTrue(Mouse.objects.filter(pk=self.mouse.pk).exists())


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
                response = self.client.get(url, follow=True)

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
            reverse("lab:edit_selected_mice_all"),
            {
                "return_url": self.return_url,
                "selected_mouse": [self.mouse.pk], "apply_bulk_edit": "1",
                f"{individual_mouse_field_prefix(self.mouse)}_mouse_id": "MOUSE-ID-RENAMED",
                "confirm_mouse_ids": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response["Location"].startswith(
                reverse("lab:edit_selected_mice_all")
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
            ("MOUSE-ID-2", "MOUSE-ID-2", "duplicate"),
            ("NEW-MOUSE-ID", "", "missing confirmation"),
            ("X" * 101, "X" * 101, "too long"),
        ]

        for new_mouse_id, confirmation, label in cases:
            with self.subTest(label=label):
                response = self.client.post(
                    reverse("lab:edit_selected_mice_all"),
                    {
                        "return_url": self.return_url,
                        "selected_mouse": [self.mouse.pk], "apply_bulk_edit": "1",
                        f"{individual_mouse_field_prefix(self.mouse)}_mouse_id": new_mouse_id,
                        "confirm_mouse_ids": "on" if confirmation else "",
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
        self.assertContains(response, "Delete animal")

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
