import json
from importlib import import_module

from django.apps import apps
from django.db import connection
from django.test import RequestFactory, TestCase

from .forms import (
    BulkMouseUpdateForm, CrossingCreateForm, MouseEditForm,
    genotype_field_name, sync_mouse_structured_genetics,
)
from .models import Crossing, Mouse, MouseLine, Protocol, Species, resolve_crossing
from .views import crossing_genotype_fields


class CrossingGeneOrderTests(TestCase):
    def setUp(self):
        self.species = Species.objects.create(name="Gene order test species")
        self.lines = [
            MouseLine.objects.create(species=self.species, name=name)
            for name in ("Zeta", "Alpha", "Beta")
        ]
        form = CrossingCreateForm({
            "species": self.species.pk,
            "selected_lines": ",".join(str(line.pk) for line in self.lines),
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.crossing, _ = form.save()

    def test_selection_order_survives_reload_and_existing_crossing_lookup(self):
        crossing = Crossing.objects.get(pk=self.crossing.pk)
        self.assertEqual(crossing.display_name, "Zeta x Alpha x Beta")
        self.assertEqual(crossing.ordered_lines(), self.lines)
        existing, created = resolve_crossing(self.species, list(reversed(self.lines)))
        self.assertFalse(created)
        self.assertEqual(existing.pk, crossing.pk)
        self.assertEqual(existing.ordered_lines(), self.lines)

    def test_edit_forms_dynamic_fields_and_summaries_share_order(self):
        protocol = Protocol.objects.create(
            protocol_number=1, licence_reference="TEST", name="Test",
        )
        mouse = Mouse.objects.create(mouse_id="GENE-ORDER-TEST", protocol=protocol)
        sync_mouse_structured_genetics(
            mouse, self.crossing,
            {line.pk: zygosity for line, zygosity in zip(self.lines, ("het", "hom", "wt"))},
        )
        mouse.refresh_from_db()
        expected_fields = [genotype_field_name(line) for line in self.lines]
        self.assertEqual(MouseEditForm(instance=mouse).genotype_fields, expected_fields)
        self.assertEqual(
            BulkMouseUpdateForm(selected_mice=[mouse]).genotype_fields, expected_fields,
        )
        self.assertEqual(mouse.genotype, "Zeta: Het; Alpha: Hom; Beta: WT")
        self.assertEqual(mouse.genetics_genotype_display, mouse.genotype)
        response = crossing_genotype_fields(RequestFactory().get(
            "/", {"crossing": self.crossing.pk, "mouse": mouse.pk},
        ))
        self.assertEqual(
            [line["field_name"] for line in json.loads(response.content)["lines"]],
            expected_fields,
        )

    def test_backfill_uses_display_order_and_membership_order_as_fallback(self):
        migration = import_module("lab.migrations.0044_crossingline_position")
        for name in ("Beta x Zeta x Alpha", "Legacy custom name"):
            with self.subTest(name=name):
                self.crossing.display_name = name
                self.crossing.save(update_fields=["display_name"])
                self.crossing.crossing_lines.update(position=0)
                migration.preserve_crossing_order(apps, connection.schema_editor())
                expected = [self.lines[2], self.lines[0], self.lines[1]] if name.startswith("Beta") else self.lines
                self.assertEqual(self.crossing.ordered_lines(), expected)
