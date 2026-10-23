from django.test import TestCase

from .forms import BulkMouseUpdateForm
from .models import Mouse, Protocol
from .views import apply_bulk_mouse_updates


class BulkMouseNotesTests(TestCase):
    def setUp(self):
        protocol = Protocol.objects.create(
            protocol_number=1, licence_reference="NOTES-TEST", name="Test",
        )
        self.mice = [
            Mouse.objects.create(
                mouse_id=f"NOTES-{index}", protocol=protocol, notes=notes,
            )
            for index, notes in enumerate(("Original A", "Original B", ""))
        ]

    def apply_notes(self, action, text):
        form = BulkMouseUpdateForm(
            {"notes_action": action, "notes": text}, selected_mice=self.mice,
        )
        self.assertTrue(form.is_valid(), form.errors)
        apply_bulk_mouse_updates(self.mice, form)
        for mouse in self.mice:
            mouse.refresh_from_db()

    def test_append_keeps_individual_notes(self):
        self.apply_notes("append", "New text")
        self.assertEqual(
            [mouse.notes for mouse in self.mice],
            ["Original A\n\nNew text", "Original B\n\nNew text", "New text"],
        )

    def test_replace_overwrites_notes(self):
        self.apply_notes("replace", "Corrected text")
        self.assertEqual([mouse.notes for mouse in self.mice], ["Corrected text"] * 3)

    def test_blank_replace_clears_notes(self):
        self.apply_notes("replace", "")
        self.assertEqual([mouse.notes for mouse in self.mice], [""] * 3)

    def test_leave_unchanged_ignores_notes_during_other_update(self):
        form = BulkMouseUpdateForm(
            {"notes_action": "", "notes": "Ignored", "update_status": "on", "status": "Culled"},
            selected_mice=self.mice,
        )
        self.assertTrue(form.is_valid(), form.errors)
        apply_bulk_mouse_updates(self.mice, form)
        for mouse, expected in zip(self.mice, ("Original A", "Original B", "")):
            mouse.refresh_from_db()
            self.assertEqual(mouse.notes, expected)
            self.assertEqual(mouse.status, "Culled")

    def test_empty_append_and_invalid_action_are_rejected(self):
        for action, text in (("append", " "), ("invalid", "text")):
            with self.subTest(action=action):
                form = BulkMouseUpdateForm(
                    {"notes_action": action, "notes": text}, selected_mice=self.mice,
                )
                self.assertFalse(form.is_valid())

    def test_leave_unchanged_alone_is_not_an_update(self):
        form = BulkMouseUpdateForm(
            {"notes": "Ignored"}, selected_mice=self.mice,
        )
        self.assertFalse(form.is_valid())
