from urllib.parse import parse_qs, urlsplit

from django.test import override_settings
from django.urls import reverse

from ..models import Experiment, Mouse, Project, Protocol, Recording, RecordingType
from ..test_navigation import Markup
from ._support import AuthenticatedTestCase


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class AnimalRecordingCreationTests(AuthenticatedTestCase):
    def setUp(self):
        self.protocol = Protocol.objects.create(protocol_number=820, licence_reference="recording-test")
        self.mice = [
            Mouse.objects.create(mouse_id=mouse_id, protocol=self.protocol, notes="Keep this note")
            for mouse_id in ("Recording animal / 1", "Recording animal & 2")
        ]
        self.recording_type = RecordingType.objects.create(name="Test imaging", slug="test-imaging")
        self.other_type = RecordingType.objects.create(name="Test behaviour", slug="test-behaviour")
        self.project = Project.objects.create(name="Recording project")
        self.other_project = Project.objects.create(name="Other recording project")
        self.experiment = Experiment.objects.create(
            project=self.project, name="Compatible imaging", recording_type=self.recording_type,
        )
        self.incompatible = Experiment.objects.create(
            project=self.project, name="Behaviour only", recording_type=self.other_type,
        )
        self.other_experiment = Experiment.objects.create(
            project=self.other_project, name="Other imaging", recording_type=self.recording_type,
        )
        self.url = reverse("lab:edit_selected_mice_all")
        self.return_url = reverse("lab:mice_page") + "?page=2"

    def selection_data(self, **overrides):
        return {
            "select_recording_destination": "1",
            "selected_mouse": [mouse.pk for mouse in self.mice],
            "return_url": self.return_url,
            "mouse_editor_tab": "services",
            "recording-recording_type": str(self.recording_type.pk),
            "recording-project": str(self.project.pk),
            "recording-experiment": str(self.experiment.pk),
            **overrides,
        }

    def test_recording_selection_has_an_independent_form_in_services_tab(self):
        response = self.client.get(self.url, {"selected_mouse": [mouse.pk for mouse in self.mice]})
        html = Markup(response.content.decode())
        form = html.find("form", id="new-animal-recording-form")[0]
        self.assertTrue(form.within("section", id="mouse-panel-services"))
        self.assertFalse(form.within("form", id="primary-form"))
        service_action = next(link for link in html.find("a") if link.text.strip() == "Add record")
        recording_action = html.find("button", **{"data-recording-continue": None})[0]
        self.assertEqual(service_action.attrs["class"], recording_action.attrs["class"])
        self.assertTrue(service_action.within("form", id="new-animal-recording-form"))
        self.assertTrue(recording_action.within("form", id="new-animal-recording-form"))
        for name in ("recording_type", "project", "experiment"):
            self.assertTrue(html.find("select", name=f"recording-{name}"))
        options = response.context["recording_destination_options"]
        self.assertIn(str(self.experiment.pk), [option["experiment"] for option in options])

    def test_destination_choices_exclude_archived_untyped_and_inactive_experiments(self):
        inactive_project = Project.objects.create(name="Inactive project", active=False)
        inactive_type = RecordingType.objects.create(name="Inactive type", slug="inactive-test", active=False)
        excluded = [
            Experiment.objects.create(project=self.project, name="Archived", recording_type=self.recording_type, archived=True),
            Experiment.objects.create(project=self.project, name="Untyped"),
            Experiment.objects.create(project=inactive_project, name="Inactive project experiment", recording_type=self.recording_type),
            Experiment.objects.create(project=self.project, name="Inactive recording type", recording_type=inactive_type),
        ]
        response = self.client.get(self.url, {"selected_mouse": self.mice[0].pk})
        choices = {option["experiment"] for option in response.context["recording_destination_options"]}
        self.assertTrue(all(str(experiment.pk) not in choices for experiment in excluded))
        types = response.context["recording_destination_form"].fields["recording_type"].queryset
        self.assertNotIn(inactive_type, types)

    def test_service_projects_are_excluded_and_cannot_be_submitted_as_recording_destinations(self):
        service_experiment = Experiment.objects.get(project__is_service=True, name="Genotyping")
        response = self.client.get(self.url, {"selected_mouse": self.mice[0].pk})
        self.assertTrue(response.context["service_recording_actions"])
        self.assertTrue(all(
            option["project"] != str(service_experiment.project_id)
            for option in response.context["recording_destination_options"]
        ))
        response = self.client.post(self.url, self.selection_data(**{
            "recording-recording_type": str(service_experiment.recording_type_id),
            "recording-project": str(service_experiment.project_id),
            "recording-experiment": str(service_experiment.pk),
        }))
        self.assertEqual(response.status_code, 200)
        form = response.context["recording_destination_form"]
        self.assertIn("project", form.errors)
        self.assertIn("experiment", form.errors)
        self.assertFalse(form.fields["project"].queryset.filter(is_service=True).exists())

    def test_service_recording_type_can_still_be_used_in_an_ordinary_project(self):
        service_experiment = Experiment.objects.get(project__is_service=True, name="Genotyping")
        experiment = Experiment.objects.create(
            name="Project genotyping", project=self.project,
            recording_type=service_experiment.recording_type,
        )
        response = self.client.post(self.url, self.selection_data(**{
            "recording-recording_type": str(service_experiment.recording_type_id),
            "recording-experiment": str(experiment.pk),
        }))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response["Location"]).path, reverse("lab:add_recording", args=[self.project.pk, experiment.pk]))

    def test_selection_opens_recording_form_with_all_animals_and_context(self):
        for url, mice in (
            (self.url, self.mice[:1]),
            (self.url, self.mice),
            (reverse("lab:edit_selected_mice_licence_protocol", args=[self.protocol.licence_reference, self.protocol.protocol_number]), self.mice),
        ):
            with self.subTest(url=url, count=len(mice)):
                response = self.client.post(url, self.selection_data(selected_mouse=[mouse.pk for mouse in mice]))
                self.assertEqual(response.status_code, 302)
                parts = urlsplit(response["Location"])
                self.assertEqual(parts.path, reverse("lab:add_recording", args=[self.project.pk, self.experiment.pk]))
                query = parse_qs(parts.query)
                return_parts = urlsplit(query["return_url"][0])
                self.assertEqual(return_parts.path, url)
                return_query = parse_qs(return_parts.query)
                self.assertEqual(return_query["selected_mouse"], [mouse.pk for mouse in mice])
                self.assertEqual(return_query["mouse_editor_tab"], ["services"])
                self.assertEqual(return_query["return_url"], [self.return_url])
                add_response = self.client.get(response["Location"])
                self.assertEqual([mouse.pk for mouse in add_response.context["selected_mice"]], [mouse.pk for mouse in mice])

    def test_incompatible_or_forged_destinations_are_rejected_without_updating_animals(self):
        for overrides in (
            {"recording-experiment": str(self.incompatible.pk)},
            {"recording-experiment": str(self.other_experiment.pk)},
            {"recording-recording_type": "invalid"},
            {"recording-project": "999999"},
            {"recording-experiment": ""},
        ):
            with self.subTest(overrides=overrides):
                response = self.client.post(self.url, self.selection_data(
                    **overrides, update_notes="on", notes="Do not apply",
                ))
                self.assertEqual(response.status_code, 200)
                form = response.context["recording_destination_form"]
                self.assertTrue(form.errors)
                self.assertNotIn(self.incompatible, form.fields["experiment"].queryset)
                self.assertNotIn(self.other_experiment, form.fields["experiment"].queryset)
                self.assertFalse(response.context["form"].is_bound)
                for mouse in self.mice:
                    mouse.refresh_from_db()
                    self.assertEqual(mouse.notes, "Keep this note")

    def test_experiment_archived_since_page_load_is_rejected(self):
        self.experiment.archived = True
        self.experiment.save(update_fields=["archived"])
        response = self.client.post(self.url, self.selection_data())
        self.assertEqual(response.status_code, 200)
        self.assertIn("experiment", response.context["recording_destination_form"].errors)

    def test_recording_can_be_saved_with_selected_animals_and_returns_to_services(self):
        response = self.client.post(self.url, self.selection_data())
        recording_url = response["Location"]
        return_url = parse_qs(urlsplit(recording_url).query)["return_url"][0]
        response = self.client.post(recording_url, {
            "return_url": return_url,
            "mice": "\n".join(mouse.pk for mouse in self.mice),
            "recording_date": "2026-10-25",
            "notes": "Recorded from the animal editor",
        })
        self.assertRedirects(response, return_url)
        recording = Recording.objects.get(experiment=self.experiment)
        self.assertEqual(recording.recording_type, self.recording_type)
        self.assertEqual(recording.owner, self.user)
        self.assertCountEqual(recording.mice.all(), self.mice)
