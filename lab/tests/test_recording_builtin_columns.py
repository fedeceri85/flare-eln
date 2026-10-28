"""Built-in recording column visibility is per type and never removes stored data."""
from datetime import date

from django.test import override_settings
from django.urls import reverse

from ..models import Experiment, Mouse, Project, Protocol, Recording, RecordingField, RecordingType
from ..test_navigation import Markup
from ._support import AuthenticatedTestCase


@override_settings(ALLOWED_HOSTS=['testserver'])
class RecordingBuiltinColumnTests(AuthenticatedTestCase):
    def setUp(self):
        self.kind = RecordingType.objects.create(name='Built-in test', plural_name='Built-in tests', slug='built-in-test')
        self.other = RecordingType.objects.create(name='Other built-in test', slug='other-built-in-test')
        RecordingField.objects.create(recording_type=self.kind, key='metric', label='Metric', data_type='text', display_in_table=True)
        self.project = Project.objects.create(name='Built-in column tests')
        self.experiment = Experiment.objects.create(project=self.project, name='Visible columns', recording_type=self.kind)
        protocol = Protocol.objects.create(protocol_number=868, licence_reference='BUILTIN', name='Built-in columns')
        self.mouse = Mouse.objects.create(mouse_id='Built-in mouse', protocol=protocol)
        self.recording = Recording.objects.create(experiment=self.experiment, recording_type=self.kind, owner=self.user,
            recording_date=date(2026, 10, 1), data_paths=['/retained/data'], notes='Retained recording note', values={'metric': 'Visible custom value'})
        self.recording.mice.add(self.mouse)
        other_experiment = Experiment.objects.create(project=self.project, name='Other columns', recording_type=self.other)
        other_recording = Recording.objects.create(experiment=other_experiment, recording_type=self.other, owner=self.user,
            recording_date=date(2026, 10, 1), data_paths=['/other/data'], notes='Other retained note')
        other_recording.mice.add(self.mouse)
        self.url = reverse('lab:recordings_page', args=[self.project.pk, self.experiment.pk])

    def test_visibility_combinations_apply_to_both_tables_and_keep_other_types_and_data(self):
        for paths, notes in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(hide_paths=paths, hide_notes=notes):
                self.kind.hide_data_paths_in_table, self.kind.hide_notes_in_table = paths, notes
                self.kind.save()
                response = self.client.get(self.url)
                table = response.context['table']
                labels = [column['label'] for column in table['columns'] if 'label' in column]
                self.assertEqual('Recording Paths' in labels, not paths)
                self.assertEqual('Notes' in labels, not notes)
                self.assertIn('Metric', labels)
                self.assertEqual(len(table['rows'][0]['cells']), len(table['columns']))
                summary = self.client.get(reverse('lab:animal_summary', args=[self.mouse.pk]))
                tables = {section['title']: section['table'] for section in summary.context['animal_recording_sections']}
                labels = [column['label'] for column in tables[self.kind.display_plural_name]['columns']]
                self.assertEqual('Paths' in labels, not paths)
                self.assertEqual('Notes' in labels, not notes)
                other_labels = [column['label'] for column in tables[self.other.display_plural_name]['columns']]
                self.assertIn('Paths', other_labels); self.assertIn('Notes', other_labels)
                self.recording.refresh_from_db()
                self.assertEqual(self.recording.data_paths, ['/retained/data'])
                self.assertEqual(self.recording.notes, 'Retained recording note')

    def test_hidden_fields_remain_editable_and_hidden_sort_keys_fall_back(self):
        self.kind.hide_data_paths_in_table = self.kind.hide_notes_in_table = True
        self.kind.save()
        edit = self.client.get(reverse('lab:edit_recording', args=[self.project.pk, self.experiment.pk, self.recording.pk]))
        self.assertContains(edit, 'name="data_paths"'); self.assertContains(edit, 'name="notes"')
        self.assertContains(edit, '/retained/data'); self.assertContains(edit, 'Retained recording note')
        for sort in ('data_paths', 'notes'):
            response = self.client.get(self.url, {'sort': sort, 'direction': 'asc'})
            date_column = next(column for column in response.context['table']['columns'] if column.get('label') == 'Date')
            self.assertEqual(date_column['sort_state'], 'desc')

    def test_creation_and_edit_forms_show_builtin_controls_and_save_independently(self):
        self.user.is_staff = True; self.user.save()
        add = reverse('lab:add_recording_type')
        edit = reverse('lab:edit_recording_type', args=[self.kind.pk])
        for url in (add, edit):
            response = self.client.get(url)
            self.assertContains(response, 'Built-in recording fields')
            markup = Markup(response.content.decode())
            for name in ('hide_data_paths_in_table', 'hide_notes_in_table'):
                controls = markup.find('input', name=name)
                self.assertEqual(len(controls), 1)
                self.assertNotIn('checked', controls[0].attrs)
        response = self.client.post(add, {'name': 'New built-in', 'slug': 'new-built-in', 'active': 'on', 'hide_data_paths_in_table': 'on'})
        self.assertEqual(response.status_code, 302)
        kind = RecordingType.objects.get(slug='new-built-in')
        self.assertTrue(kind.hide_data_paths_in_table); self.assertFalse(kind.hide_notes_in_table)
        edit = reverse('lab:edit_recording_type', args=[kind.pk])
        self.assertEqual(self.client.post(edit, {'name': kind.name, 'slug': kind.slug, 'active': 'on', 'hide_notes_in_table': 'on'}).status_code, 302)
        kind.refresh_from_db()
        self.assertFalse(kind.hide_data_paths_in_table); self.assertTrue(kind.hide_notes_in_table)
        invalid = self.client.post(edit, {'name': '', 'slug': kind.slug, 'hide_data_paths_in_table': 'on'})
        self.assertEqual(invalid.status_code, 200)
        self.assertTrue(invalid.context['form']['hide_data_paths_in_table'].value())
