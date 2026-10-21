from datetime import date
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from ..models import Experiment, Project, Recording, RecordingType


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class AuthenticationTests(TestCase):
    def test_lab_pages_require_login(self):
        response = self.client.get(reverse("lab:projects_page"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            "/login/?next=/projects/",
        )

    def test_user_can_log_in_and_out(self):
        User = get_user_model()
        User.objects.create_user(
            username="login-user",
            password="password",
        )

        response = self.client.post(
            reverse("lab:login"),
            {
                "username": "login-user",
                "password": "password",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/")

        response = self.client.post(reverse("lab:logout"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/login/")

    def test_staff_home_page_shows_admin_console_card(self):
        User = get_user_model()
        staff_user = User.objects.create_user(
            username="staff-user",
            password="password",
            is_staff=True,
        )
        self.client.force_login(staff_user)

        response = self.client.get(reverse("lab:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Admin Console")
        self.assertContains(response, 'href="/admin/"')
        self.assertContains(response, "Lines &amp; Crossings")
        self.assertContains(response, 'href="/lines-crossings/"')

    def test_normal_home_page_hides_admin_console_card(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="normal-user",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("lab:home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Admin Console")
        self.assertNotContains(response, "Lines &amp; Crossings")


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class ProjectAdminTests(TestCase):
    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser(
            username="project-admin",
            password="password",
        )
        self.client.force_login(self.admin_user)

    def test_projects_are_managed_outside_django_admin(self):
        self.assertFalse(admin.site.is_registered(Project))

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '/admin/lab/project/')

    def test_legacy_project_admin_urls_are_unavailable(self):
        services_project = Project.objects.get(name="Services")
        project = Project.objects.create(name="Temporary admin project")

        for target in (services_project, project):
            with self.subTest(project=target.name):
                response = self.client.post(
                    f"/admin/lab/project/{target.pk}/delete/",
                    {"post": "yes"},
                )
                self.assertEqual(response.status_code, 404)
                self.assertTrue(Project.objects.filter(pk=target.pk).exists())


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class RecordingAdminTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin_user = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(self.admin_user)
        self.project = Project.objects.create(name="Admin project")
        self.recording_type = RecordingType.objects.get(slug="imaging")
        self.experiment = Experiment.objects.create(
            project=self.project,
            name="Admin experiment",
            recording_type=self.recording_type,
        )
        self.recording = Recording.objects.create(
            experiment=self.experiment,
            recording_type=self.recording_type,
            recording_date=date(2026, 7, 20),
            data_paths=["/data/admin/recording"],
        )

    def test_admin_can_open_recording_type_add_page_with_field_inline(self):
        response = self.client.get(reverse("admin:lab_recordingtype_add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recording fields")
        self.assertContains(response, 'name="fields-0-key"')
        self.assertContains(response, 'name="fields-0-data_type"')

    def test_recordings_are_managed_outside_django_admin(self):
        self.assertFalse(admin.site.is_registered(Recording))

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '/admin/lab/recording/')

        for path in (
            "/admin/lab/recording/",
            "/admin/lab/recording/add/",
            f"/admin/lab/recording/{self.recording.pk}/change/",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)

        response = self.client.post(
            f"/admin/lab/recording/{self.recording.pk}/change/",
            {"data_paths": "/data/admin/changed"},
        )
        self.assertEqual(response.status_code, 404)
        self.recording.refresh_from_db()
        self.assertEqual(self.recording.data_paths, ["/data/admin/recording"])
