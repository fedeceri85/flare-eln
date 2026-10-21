from datetime import date
from django.test import override_settings
from django.urls import reverse
from ..models import Experiment, Project, RecordingType
from ._support import AuthenticatedTestCase


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
        self.assert_contextual_link(
            response, self.experiments_url, reverse("lab:projects_page"),
        )

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
