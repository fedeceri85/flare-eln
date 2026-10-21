"""Shared fixtures for feature tests; contains no runnable test methods."""

from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase
from ..test_navigation import Markup
from ..models import MouseLine, Recording, Species, resolve_crossing


class AuthenticatedTestCase(TestCase):
    def _pre_setup(self):
        super()._pre_setup()
        self.user = get_user_model().objects.create_user(
            username=f"user-{self._testMethodName}",
            password="password",
        )
        self.client.force_login(self.user)

    def assert_contextual_link(self, response, target, return_url):
        target_parts = urlsplit(target)
        expected_query = parse_qs(target_parts.query)
        expected_query["return_url"] = [return_url]
        links = Markup(response.content.decode()).find("a")
        for link in links:
            parts = urlsplit(link.attrs.get("href", ""))
            query = parse_qs(parts.query)
            if (
                parts.path == target_parts.path
                and parts.fragment == target_parts.fragment
                and all(query.get(key) == value for key, value in expected_query.items())
            ):
                return
        self.fail(f"Missing link to {target!r} preserving return URL {return_url!r}")



def create_recording(*, mouse=None, mice=None, **kwargs):
    if mouse is not None:
        if mice is not None:
            raise ValueError("Use either mouse or mice, not both.")

        mice = [mouse]

    recording = Recording.objects.create(**kwargs)

    if mice:
        recording.mice.set(mice)

    return recording


def create_test_crossing(
    line_names=("Gad2",),
    species_name="Mouse",
    wild_type_names=(),
):
    species, _ = Species.objects.get_or_create(name=species_name)
    lines = []

    for line_name in line_names:
        line, _ = MouseLine.objects.get_or_create(
            species=species,
            name=line_name,
            defaults={"is_wild_type": line_name in wild_type_names},
        )
        lines.append(line)

    crossing, _ = resolve_crossing(species, lines)
    return crossing, lines


def crossing_post_data(crossing, lines, zygosity="het"):
    data = {
        "crossing_definition": str(crossing.pk),
        "crossing_search": f"{crossing.species.name} - {crossing.display_name}",
    }

    for line in lines:
        data[f"genotype_line_{line.pk}"] = zygosity

    return data
