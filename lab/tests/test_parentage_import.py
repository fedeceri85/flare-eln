"""Parent matching stays within mapping, Animals and Review."""
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from ..models import Mouse, MouseLine, Species, Protocol, Project, Experiment, RecordingType, SpreadsheetImportDraft, Recording
from ..parentage import apply_parentage, graph_state, validate_parentage
from ..test_navigation import Markup
from ..spreadsheet_import.engine import animal_groups, build_review, commit_import, draft_table, initial_resolution
from ..spreadsheet_import.forms import AnimalsForm
from ..spreadsheet_import.mapping import new_definition
from ..spreadsheet_import.source import read_upload, table_from_sheet


@override_settings(ALLOWED_HOSTS=['testserver'])
class ParentageImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='parent-import')
        self.client.force_login(self.user)
        self.protocol = Protocol.objects.create(protocol_number=899, licence_reference='PARENT-IMPORT', name='Parent import')
        self.line = MouseLine.objects.create(species=Species.objects.get(name='Mouse'), name='Parent import line')
        self.crossing = self.line.crossings.get()
        self.mother = Mouse.objects.create(mouse_id='M-001', protocol=self.protocol, sex='F')
        self.child = Mouse.objects.create(mouse_id='Child', protocol=self.protocol, crossing_definition=self.crossing)
        self.project = Project.objects.create(name='Parent import')
        self.kind = RecordingType.objects.create(name='Parent test', slug='parent-test')
        self.experiment = Experiment.objects.create(project=self.project, name='Parents', recording_type=self.kind)

    def draft(self, text='Date,Mouse ID,Mum ID,Dad ID\n28.04.26,Child,M-001,00123\n'):
        source = read_upload(SimpleUploadedFile('parents.csv', text.encode()))
        table = table_from_sheet(source[0], 1)
        definition = new_definition(self.experiment, table['columns'])
        definition['defaults'].update(protocol_id=self.protocol.pk, crossing_id=self.crossing.pk)
        draft = SpreadsheetImportDraft.objects.create(owner=self.user, experiment=self.experiment, filename='parents.csv',
            source=source, configuration=definition, state={'sheet': 'CSV', 'header_row': 1, 'selected_rows': [r['number'] for r in table['rows']]})
        draft.state['animals'] = {g['key']: initial_resolution(g, definition) for g in animal_groups(draft)}
        draft.save()
        return draft

    def review(self, draft):
        preview = build_review(draft)
        self.assertEqual(preview['errors'], [])
        draft.state['review_fingerprint'] = preview['fingerprint']; draft.save()
        return preview

    def commit(self, draft, approvals=()):
        return commit_import(draft.pk, self.user, self.experiment, approvals)

    def test_import_keeps_outside_links_for_maintenance_and_shares_validation_snapshot(self):
        Mouse.objects.bulk_create([Mouse(mouse_id=f"Outside-{i:03}", protocol=self.protocol, mother_external_id=self.mother.pk) for i in range(60)])
        draft = self.draft("Date,Mouse ID,Mum ID,Dad ID\n28.04.26,M-001,,\n")
        preview = self.review(draft)
        self.assertEqual(preview['reconciliations'], [])
        with patch('lab.parentage.graph_state', wraps=graph_state) as reads, patch('lab.spreadsheet_import.parentage.validate_parentage', wraps=validate_parentage) as validations:
            self.commit(draft)
        self.assertEqual(reads.call_count, 1)
        self.assertEqual(validations.call_count, 2)  # Baseline fingerprint and approved final graph.
        self.assertEqual(Mouse.objects.filter(mouse_id__startswith='Outside-', mother=self.mother).count(), 0)

    def test_aliases_and_leading_zero_text_are_mapped(self):
        for mother, father in [('Mum ID', 'Dad ID'), ('Dam', 'Sire'), ('mother', 'father')]:
            draft = self.draft(f'Date,Mouse ID,{mother},{father}\n28.04.26,Child,M-001,00123\n')
            self.assertIn('animal:mother_external_id', draft.configuration['bindings'])
            preview = self.review(draft)
            self.assertEqual([p['status'] for p in preview['animals'][0]['parents']], ['Linked existing', 'External text'])
            self.assertEqual(preview['animals'][0]['parents'][1]['text'], '00123')

    def test_animals_step_offers_parent_autocomplete_without_new_form_fields(self):
        draft = self.draft()
        key = animal_groups(draft)[0]['key']
        response = self.client.get(reverse('lab:import_recordings_draft', args=[self.project.pk, self.experiment.pk, draft.pk]), {'step': 'animals'})
        self.assertEqual(response.status_code, 200)
        markup = Markup(response.content.decode())
        for role in ('mother', 'father'):
            field = markup.find('input', name=f'{key}_{role}_external_id')[0]
            self.assertEqual(field.attrs['data-parent-lookup'], reverse('lab:mouse_lookup'))
            self.assertTrue(field.within('div', **{'data-parent-import': 'true'}))
        self.assertEqual(len(markup.find('div', **{'data-parent-suggestions': None})), 2)
        self.assertContains(response, 'lab/parentage.js')
        # Suggestion selections fill the existing ID override; review remains authoritative.
        form = AnimalsForm(draft, draft_table(draft), data={
            f'{key}_mode': 'link', f'{key}_mouse_id': self.child.pk,
            f'{key}_mother_external_id': self.mother.pk, f'{key}_mother_text_only': 'on',
        })
        self.assertTrue(form.is_valid(), form.errors)
        form.apply(draft)
        self.assertEqual(draft.state['animals'][key]['details']['mother_external_id'], self.mother.pk)
        self.assertTrue(draft.state['animals'][key]['parent_text_only']['mother'])

    def test_existing_parent_pair_is_one_change_and_needs_approval(self):
        draft = self.draft(); preview = self.review(draft)
        parent_changes = [change for change in preview['changes'] if change['field'].startswith('parent:')]
        self.assertEqual(len(parent_changes), 2)
        mother_change = next(c for c in parent_changes if c['field'] == 'parent:mother')
        self.commit(draft, [mother_change['token']])
        self.child.refresh_from_db()
        self.assertEqual(self.child.mother_id, self.mother.pk)
        self.assertEqual(self.child.mother_external_id, 'M-001')
        self.assertEqual(self.child.father_external_id, '')

    def test_unapproved_parent_changes_do_not_write(self):
        draft = self.draft(); self.review(draft); self.commit(draft)
        self.child.refresh_from_db()
        self.assertIsNone(self.child.mother_id)
        self.assertEqual(self.child.mother_external_id, '')

    def test_blank_cells_do_not_clear_existing_parents(self):
        apply_parentage({self.child.pk: {'mother_id': self.mother.pk, 'mother_external_id': 'Old recorded ID'}})
        draft = self.draft('Date,Mouse ID,Mum ID,Dad ID\n28.04.26,Child,,\n')
        preview = self.review(draft)
        self.assertFalse(any(c['field'].startswith('parent:') for c in preview['changes']))
        self.commit(draft)
        self.child.refresh_from_db()
        self.assertEqual(self.child.mother_id, self.mother.pk)

    def test_new_child_automatic_link_and_external_text(self):
        draft = self.draft('Date,Mouse ID,Mum ID,Dad ID\n28.04.26,New child,m-001,00123\n')
        self.review(draft); result = self.commit(draft)
        child = Mouse.objects.get(pk='New child')
        self.assertEqual(child.mother_id, self.mother.pk)
        self.assertEqual(child.mother_external_id, 'm-001')
        self.assertEqual(child.father_external_id, '00123')
        self.assertEqual(result['created_animals'], 1)
        self.assertEqual(self.commit(draft), result)

    def test_same_file_parent_in_either_order(self):
        for child_id, parent_id, reverse_rows in [('Pup 1', 'New mum 1', False), ('Pup 2', 'New mum 2', True)]:
            rows = [f'28.04.26,{child_id},{parent_id},', f'28.04.26,{parent_id},,']
            if reverse_rows: rows.reverse()
            draft = self.draft('Date,Mouse ID,Mum ID,Dad ID\n' + '\n'.join(rows) + '\n')
            preview = self.review(draft)
            child = next(a for a in preview['animals'] if a['mouse_id'] == child_id)
            self.assertEqual(child['parents'][0]['status'], 'Linked in this import')
            self.commit(draft)
            self.assertEqual(Mouse.objects.get(pk=child_id).mother_id, parent_id)

    def test_ambiguous_match_stays_external(self):
        Mouse.objects.create(mouse_id='m-001', protocol=self.protocol)
        draft = self.draft('Date,Mouse ID,Mum ID\n28.04.26,New child,M-001\n')
        preview = self.review(draft)
        self.assertTrue(any('ambiguous' in warning for warning in preview['warnings']))
        self.commit(draft)
        self.assertIsNone(Mouse.objects.get(pk='New child').mother_id)

    def test_text_only_override_persists_in_animals_step(self):
        draft = self.draft(); table = draft_table(draft)
        group = animal_groups(draft)[0]; key = group['key']
        form = AnimalsForm(draft, table, data={f'{key}_mode': 'link', f'{key}_mouse_id': self.child.pk, f'{key}_mother_text_only': 'on'})
        self.assertTrue(form.is_valid(), form.errors)
        form.apply(draft); draft.save()
        self.assertTrue(draft.state['animals'][key]['parent_text_only']['mother'])
        preview = self.review(draft)
        mother_change = next(c for c in preview['changes'] if c['field'] == 'parent:mother')
        self.commit(draft, [mother_change['token']])
        self.child.refresh_from_db()
        self.assertIsNone(self.child.mother_id)
        self.assertEqual(self.child.mother_external_id, self.mother.pk)

    def test_parent_override_is_optional_and_applied_before_matching(self):
        draft = self.draft(); key = animal_groups(draft)[0]['key']
        draft.state['animals'][key]['details'] = {'mother_external_id': 'Unknown mum'}
        preview = self.review(draft)
        self.assertEqual(preview['animals'][0]['parents'][0]['status'], 'External text')

    def test_conflicting_parent_rows_require_correction(self):
        draft = self.draft('Date,Mouse ID,Mum ID\n28.04.26,Child,M-001\n28.04.26,Child,Other mum\n')
        self.assertTrue(any('disagree' in error for error in build_review(draft)['errors']))

    def test_cycle_errors_can_be_resolved_with_text_only_override(self):
        draft = self.draft('Date,Mouse ID,Mum ID\n28.04.26,New 1,New 2\n28.04.26,New 2,New 1\n')
        self.assertTrue(any('cycle' in error for error in build_review(draft)['errors']))
        key = animal_groups(draft)[0]['key']; draft.state['animals'][key]['parent_text_only'] = {'mother': True}
        self.review(draft); self.commit(draft)
        self.assertIsNone(Mouse.objects.get(pk='New 1').mother_id)
        self.assertEqual(Mouse.objects.get(pk='New 2').mother_id, 'New 1')

    def test_replacing_parent_cannot_retain_old_reference(self):
        apply_parentage({self.child.pk: {'mother_id': self.mother.pk, 'mother_external_id': self.mother.pk}})
        draft = self.draft('Date,Mouse ID,Mum ID\n28.04.26,Child,External replacement\n')
        preview = self.review(draft)
        token = next(c['token'] for c in preview['changes'] if c['field'] == 'parent:mother')
        self.commit(draft, [token]); self.child.refresh_from_db()
        self.assertIsNone(self.child.mother_id)
        self.assertEqual(self.child.mother_external_id, 'External replacement')

    def test_outside_reconciliation_is_available_only_through_maintenance(self):
        from ..family_maintenance import scan_missing_family_links, resolve_missing_family_links
        apply_parentage({self.child.pk: {'mother_external_id': 'New mother'}})
        draft = self.draft('Date,Mouse ID\n28.04.26,New mother\n')
        preview = self.review(draft)
        self.assertEqual(preview['reconciliations'], [])
        result = self.commit(draft); self.child.refresh_from_db()
        self.assertIsNone(self.child.mother_id)
        self.assertEqual(result['updated_animals'], 0)
        scan = scan_missing_family_links()
        keys = [entry['key'] for entry in scan['entries'] if entry['eligible']]
        resolve_missing_family_links(scan['fingerprint'], keys)
        self.child.refresh_from_db(); self.assertEqual(self.child.mother_id, 'New mother')

    def test_outside_reconciliation_defaults_to_unchanged(self):
        apply_parentage({self.child.pk: {'mother_external_id': 'New mother'}})
        draft = self.draft('Date,Mouse ID\n28.04.26,New mother\n')
        self.review(draft); self.commit(draft); self.child.refresh_from_db()
        self.assertIsNone(self.child.mother_id)

    def test_parent_data_and_candidate_changes_invalidate_review(self):
        for field, value in [('sex', 'M'), ('mother_external_id', 'Changed')]:
            draft = self.draft(); self.review(draft)
            Mouse.objects.filter(pk=self.mother.pk).update(**{field: value})
            with self.assertRaisesMessage(ValidationError, 'changed after review'):
                self.commit(draft)
        self.assertEqual(Recording.objects.count(), 0)

    def test_approval_dependent_cycle_is_checked(self):
        # Both removals/additions are valid together; approving only the addition is not.
        apply_parentage({self.child.pk: {'mother_id': self.mother.pk, 'mother_external_id': self.mother.pk}})
        draft = self.draft('Date,Mouse ID,Mum ID\n28.04.26,Child,External\n28.04.26,M-001,Child\n')
        preview = self.review(draft)
        addition = next(c['token'] for c in preview['changes'] if c['field'] == 'parent:mother' and c['new']['mother_id'] == self.child.pk)
        with self.assertRaisesMessage(ValidationError, 'cycle'):
            self.commit(draft, [addition])
        self.assertEqual(Recording.objects.count(), 0)

    def test_review_renders_parent_states_without_new_tab(self):
        draft = self.draft()
        url = reverse('lab:import_recordings_draft', args=[self.project.pk, self.experiment.pk, draft.pk])
        response = self.client.get(url, {'step': 'review'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Linked existing')
        self.assertContains(response, 'External text')
        response = self.client.get(url, {'step': 'animals'})
        self.assertContains(response, 'Keep mother as external text')

    def test_no_parent_columns_requires_no_parent_actions(self):
        draft = self.draft('Date,Mouse ID\n28.04.26,Child\n')
        preview = self.review(draft)
        self.assertEqual(preview['animals'][0]['parents'], [])
        self.assertEqual(preview['reconciliations'], [])
        self.commit(draft)

    def test_generated_id_coincidence_does_not_link_or_offer_reconciliation(self):
        draft = self.draft('Date,Mouse ID,Local ID,Birth date,Breeding pair,Mum ID\n28.04.26,,LE,01.01.26,BP,\n28.04.26,New child,,,,\n')
        provisional = self.review(draft)
        generated = next(a['mouse_id'] for a in provisional['animals'] if a['generate_id'])
        group = next(g for g in animal_groups(draft) if g['animal_id'] == 'New child')
        draft.state['animals'][group['key']]['details'] = {'mother_external_id': generated}
        apply_parentage({self.child.pk: {'mother_external_id': generated}})
        preview = self.review(draft)
        child = next(a for a in preview['animals'] if a['mouse_id'] == 'New child')
        self.assertEqual(child['parents'][0]['status'], 'External text')
        self.assertEqual(preview['reconciliations'], [])
        self.commit(draft)
        self.assertIsNone(Mouse.objects.get(pk='New child').mother_id)

    def test_unrelated_animal_edits_do_not_invalidate_an_import_review(self):
        unrelated = Mouse.objects.create(mouse_id='Unrelated', protocol=self.protocol)
        draft = self.draft(); preview = self.review(draft)
        Mouse.objects.filter(pk=unrelated.pk).update(sex='M')
        token = next(c['token'] for c in preview['changes'] if c['field'] == 'parent:mother')
        self.commit(draft, [token]); self.child.refresh_from_db()
        self.assertEqual(self.child.mother_id, self.mother.pk)
