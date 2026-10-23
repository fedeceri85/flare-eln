from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from . import test_litters
from ..forms import MouseForm, MouseEditForm, BulkMouseUpdateForm
from ..management.commands.import_mice import Command
from ..models import Mouse
from ..views.mice import rename_mouse_identity


@override_settings(ALLOWED_HOSTS=['testserver'])
class MouseAddedByTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='creator')
        self.other = get_user_model().objects.create_user(username='editor')
        self.client.force_login(self.user)
        # Reuse data setup, without inheriting and re-running another test suite.
        test_litters.AddLitterTests.setUp(self)

    def payload(self, **kwargs):
        return test_litters.AddLitterTests.litter_payload(self, **kwargs)

    def create(self, **kwargs):
        return Mouse.objects.create(mouse_id='EXISTING', protocol=self.protocol, **kwargs)

    def test_litter_creation_records_signed_in_user_for_every_mouse(self):
        response = self.client.post(self.add_litter_url, self.payload(added_by=self.other.pk, added_by_id=self.other.pk))
        self.assertEqual(response.status_code, 302)
        mice = list(Mouse.objects.all())
        self.assertEqual(len(mice), 2)
        self.assertTrue(all(mouse.added_by_id == self.user.pk for mouse in mice))

    def test_single_mouse_uses_same_creator_path(self):
        response = self.client.post(reverse('lab:add_litter_all'), self.payload(mouse_numbers='12'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Mouse.objects.get().added_by, self.user)

    def test_edits_preserve_original_creator_despite_forged_post(self):
        mouse = self.create(added_by=self.user)
        self.client.force_login(self.other)
        response = self.client.post(reverse('lab:edit_selected_mice_all'), {
            'apply_bulk_edit': '1', 'selected_mouse': [mouse.pk], 'notes_action': 'replace', 'notes': 'Updated notes',
            'added_by': self.other.pk, 'added_by_id': self.other.pk,
        })
        self.assertEqual(response.status_code, 302, response.context['form'].errors if response.context else '')
        mouse.refresh_from_db()
        self.assertEqual(mouse.notes, 'Updated notes')
        self.assertEqual(mouse.added_by_id, self.user.pk)
        for form in (MouseForm(), MouseEditForm(instance=mouse), BulkMouseUpdateForm(selected_mice=[mouse])):
            self.assertNotIn('added_by', form.fields)

    def test_mouse_renaming_preserves_creator(self):
        mouse = self.create(added_by=self.user)
        renamed = rename_mouse_identity(mouse.pk, 'RENAMED')
        self.assertEqual(renamed.added_by_id, self.user.pk)
        self.assertFalse(Mouse.objects.filter(pk='EXISTING').exists())

    def test_deactivating_or_deleting_user_preserves_mouse(self):
        mouse = self.create(added_by=self.other)
        self.other.is_active = False
        self.other.save(update_fields=['is_active'])
        mouse.refresh_from_db()
        self.assertEqual(mouse.added_by_id, self.other.pk)
        self.other.delete()
        mouse.refresh_from_db()
        self.assertIsNone(mouse.added_by_id)

    def test_both_tables_show_creator_and_unknown_without_extra_account_details(self):
        self.create(added_by=self.other)
        Mouse.objects.create(mouse_id='UNKNOWN', protocol=self.protocol)
        for url in (reverse('lab:mice_page'), self.protocol_url):
            page = self.client.get(url, {'show_current_year': '0'})
            self.assertEqual(page.status_code, 200)
            table = page.context['table']
            labels = [col.get('label') for col in table['columns']]
            index = labels.index('Added by')
            values = [str(row['cells'][index]['value']) for row in table['rows']]
            self.assertIn(self.other.username, values)
            self.assertIn('&mdash;', values)

    def test_creator_sorting_works_in_both_tables(self):
        self.create(added_by=self.user)
        Mouse.objects.create(mouse_id='OTHER', protocol=self.protocol, added_by=self.other)
        for url in (reverse('lab:mice_page'), self.protocol_url):
            for direction, expected in (('asc', ['creator', 'editor']), ('desc', ['editor', 'creator'])):
                page = self.client.get(url, {'sort': 'added_by__username', 'direction': direction, 'show_current_year': '0'})
                table = page.context['table']
                index = [col.get('label') for col in table['columns']].index('Added by')
                self.assertEqual([row['cells'][index]['value'] for row in table['rows']], expected)

    def import_rows(self, username=None):
        command = Command()
        values = {command.COL_MOUSE_ID: '99991', command.COL_WT: 'WT', command.COL_SINGLE_MUTATION: None,
                  command.COL_CROSSING: None, command.COL_LAB_MEMBER: 'Spreadsheet initials', command.COL_LAB_BOOK_PAGE: '',
                  command.COL_PROTOCOL_START: None, command.COL_CULL_DATE: None}
        frame = pd.DataFrame([values, values])  # Importer skips its first data row.
        with TemporaryDirectory() as directory:
            workbook = Path(directory) / 'import.xlsx'
            workbook.touch()
            with patch('lab.management.commands.import_mice.pd.read_excel', return_value=frame):
                call_command('import_mice', excel_file=workbook, sheet_name='Test', protocol_number=self.protocol.protocol_number,
                             header_row=0, added_by=username, stdout=StringIO())
        return Mouse.objects.get(pk='99991')

    def test_import_can_attribute_new_mice_explicitly_without_overwriting_on_reimport(self):
        mouse = self.import_rows(self.user.username)
        self.assertEqual(mouse.added_by_id, self.user.pk)
        mouse = self.import_rows(self.other.username)
        self.assertEqual(mouse.added_by_id, self.user.pk)

    def test_import_without_account_keeps_unknown_and_reimport_does_not_guess_creator(self):
        mouse = self.import_rows()
        self.assertIsNone(mouse.added_by_id)
        mouse = self.import_rows(self.user.username)
        self.assertIsNone(mouse.added_by_id)

    def test_unknown_import_account_rejected_before_writes(self):
        with self.assertRaisesMessage(CommandError, 'account does not exist'):
            self.import_rows('missing-account')
        self.assertFalse(Mouse.objects.exists())
