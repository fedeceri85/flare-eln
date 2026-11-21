from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from ..models import Experiment, Mouse, Project, Protocol, Recording, RecordingType


@override_settings(ALLOWED_HOSTS=["testserver"])
class RecordingsPageAgeFilterTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="age-filter")
        self.client.force_login(self.user)
        self.protocol = Protocol.objects.create(
            protocol_number=99,
            licence_reference="AGE-LICENCE",
            name="Age filter protocol",
        )
        self.project = Project.objects.create(name="Age filter study")
        self.recording_type = RecordingType.objects.create(
            name="Age filter recording",
            slug="age-filter-recording",
        )
        self.experiment = Experiment.objects.create(
            name="Age filter experiment",
            project=self.project,
            recording_type=self.recording_type,
        )
        self.recording_date = date(2026, 1, 1)
        # Mice at known postnatal ages on self.recording_date.
        self.mouse_p0 = Mouse.objects.create(
            mouse_id="AGE-000",
            protocol=self.protocol,
            date_of_birth=date(2026, 1, 1),
        )
        self.mouse_p10 = Mouse.objects.create(
            mouse_id="AGE-010",
            protocol=self.protocol,
            date_of_birth=date(2025, 12, 22),
        )
        self.mouse_p31 = Mouse.objects.create(
            mouse_id="AGE-031",
            protocol=self.protocol,
            date_of_birth=date(2025, 12, 1),
        )
        self.mouse_p61 = Mouse.objects.create(
            mouse_id="AGE-061",
            protocol=self.protocol,
            date_of_birth=date(2025, 11, 1),
        )
        self.mouse_p92 = Mouse.objects.create(
            mouse_id="AGE-092",
            protocol=self.protocol,
            date_of_birth=date(2025, 10, 1),
        )
        self.mouse_unknown = Mouse.objects.create(
            mouse_id="AGE-NOB",
            protocol=self.protocol,
        )

    def create_recording(self, mice):
        recording = Recording.objects.create(
            experiment=self.experiment,
            recording_type=self.recording_type,
            owner=self.user,
            recording_date=self.recording_date,
        )
        recording.mice.set(mice)
        return recording

    def filtered_count(self, **params):
        response = self.client.get(
            reverse("lab:recordings_page", args=[self.project.pk, self.experiment.pk]),
            params,
        )
        self.assertEqual(response.status_code, 200, params)
        return response.context["record_count"]["count"]

    def test_includes_recording_when_mouse_age_is_within_range(self):
        self.create_recording([self.mouse_p31])
        self.assertEqual(self.filtered_count(age_from="10", age_to="60"), 1)
        self.assertEqual(self.filtered_count(age_from="0", age_to="60"), 1)

    def test_excludes_recording_when_no_mouse_is_in_range(self):
        self.create_recording([self.mouse_p92])
        self.assertEqual(self.filtered_count(age_from="10", age_to="60"), 0)

    def test_age_range_boundaries_are_inclusive(self):
        self.create_recording([self.mouse_p10])
        self.assertEqual(self.filtered_count(age_from="10"), 1)
        self.assertEqual(self.filtered_count(age_to="10"), 1)
        self.assertEqual(self.filtered_count(age_from="11"), 0)
        self.assertEqual(self.filtered_count(age_to="9"), 0)

    def test_newborn_age_zero_is_matchable(self):
        self.create_recording([self.mouse_p0])
        self.assertEqual(self.filtered_count(age_from="0", age_to="0"), 1)
        self.assertEqual(self.filtered_count(age_from="1"), 0)

    def test_any_mouse_in_range_includes_the_recording(self):
        self.create_recording([self.mouse_p92, self.mouse_p10])
        self.assertEqual(self.filtered_count(age_from="0", age_to="60"), 1)
        self.assertEqual(self.filtered_count(age_from="80", age_to="100"), 1)
        self.assertEqual(self.filtered_count(age_from="40", age_to="70"), 0)

    def test_unknown_birth_date_never_matches_an_age_range(self):
        self.create_recording([self.mouse_unknown])
        self.assertEqual(self.filtered_count(age_from="0", age_to="99999"), 0)

    def test_min_age_only_and_max_age_only(self):
        self.create_recording([self.mouse_p31])
        self.assertEqual(self.filtered_count(age_from="20"), 1)
        self.assertEqual(self.filtered_count(age_from="40"), 0)
        self.assertEqual(self.filtered_count(age_to="61"), 1)
        self.assertEqual(self.filtered_count(age_to="30"), 0)

    def test_mouse_filter_and_age_filter_combine_independently(self):
        # Both filters mean "the recording involves a mouse satisfying it",
        # so they compose independently (AGE-010 is age 10, AGE-092 is age 92).
        self.create_recording([self.mouse_p10, self.mouse_p92])
        # Involves AGE-092 and involves a mouse aged 0-20 (AGE-010, age 10).
        self.assertEqual(
            self.filtered_count(mouse="AGE-092", age_from="0", age_to="20"),
            1,
        )
        # Involves AGE-092, but no mouse is aged 50-70 (ages are 10 and 92).
        self.assertEqual(
            self.filtered_count(mouse="AGE-092", age_from="50", age_to="70"),
            0,
        )
        # No mouse matches AGE-999, so it is excluded regardless of the age range.
        self.assertEqual(
            self.filtered_count(mouse="AGE-999", age_from="0", age_to="99999"),
            0,
        )
