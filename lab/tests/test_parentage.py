"""Parent pairs, graph safety, editing, family navigation and reconciliation."""
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from ..family import build_family_sections
from ..forms import BulkMouseUpdateForm, individual_mouse_field_prefix
from ..models import Mouse, Protocol, MouseLine, Species
from ..parentage import apply_parentage, graph_state, matching_candidates, reconcile, reconciliation_fingerprint, validate_parentage
from ..views.mice import apply_bulk_mouse_updates, rename_mouse_identity


@override_settings(ALLOWED_HOSTS=['testserver'])
class ParentageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='family')
        self.client.force_login(self.user)
        self.protocol = Protocol.objects.create(protocol_number=891, licence_reference='FAMILY', name='Family')
        self.mother = Mouse.objects.create(mouse_id='Mother / 001', protocol=self.protocol, sex='F', date_of_birth=date(2025, 1, 1))
        self.father = Mouse.objects.create(mouse_id='Father', protocol=self.protocol, sex='M')
        self.child = Mouse.objects.create(mouse_id='Child', protocol=self.protocol, date_of_birth=date(2026, 1, 1))

    def link(self, **extra):
        return apply_parentage({self.child.pk: {'mother_id': self.mother.pk, 'mother_external_id': 'Recorded mother', **extra}})

    def payload(self, mouse=None, **extra):
        mouse = mouse or self.child
        return {'selected_mouse': [mouse.pk], 'apply_bulk_edit': '1',
                individual_mouse_field_prefix(mouse) + '_mouse_id': mouse.pk, **extra}

    def test_external_and_mixed_parentage_and_cross_protocol(self):
        other = Protocol.objects.create(protocol_number=892, licence_reference='FAMILY', name='Other')
        self.mother.protocol = other
        self.mother.save()
        self.link(father_external_id='00123', father_id=None)
        self.child.refresh_from_db()
        self.assertEqual(self.child.mother, self.mother)
        self.assertEqual(self.child.father_external_id, '00123')
        self.assertIsNone(self.child.father)

    def test_self_duplicate_and_indirect_cycles_are_blocked(self):
        for fields in [{'mother_id': self.child.pk}, {'mother_id': self.mother.pk, 'father_id': self.mother.pk}]:
            with self.assertRaises(ValidationError):
                apply_parentage({self.child.pk: fields})
        self.link()
        with self.assertRaisesMessage(ValidationError, 'cycle'):
            apply_parentage({self.mother.pk: {'father_id': self.child.pk}})
        self.mother.refresh_from_db()
        self.assertIsNone(self.mother.father_id)

    def test_batch_graph_checks_final_state_instead_of_intermediate_state(self):
        self.link()
        apply_parentage({self.child.pk: {'mother_id': None}, self.mother.pk: {'mother_id': self.child.pk}})
        self.mother.refresh_from_db()
        self.assertEqual(self.mother.mother_id, self.child.pk)
        with self.assertRaisesMessage(ValidationError, 'cycle'):
            validate_parentage({'new-1': {'mother_id': 'new-2'}, 'new-2': {'father_id': 'new-1'}})

    def test_sex_date_warnings_allow_saving(self):
        self.mother.sex = 'M'
        self.mother.date_of_birth = date(2026, 1, 1)
        self.mother.save()
        warnings = self.link()
        self.assertEqual(len(warnings), 2)
        self.child.refresh_from_db()
        self.assertEqual(self.child.mother_id, self.mother.pk)

    def test_long_lineage_does_not_hit_python_recursion_limit(self):
        changes = {str(i): {'mother_id': str(i-1) if i else None} for i in range(1500)}
        self.assertEqual(validate_parentage(changes, {}), [])

    def test_rename_and_delete_keep_recorded_text(self):
        self.link()
        renamed = rename_mouse_identity(self.mother.pk, 'Renamed mother')
        self.child.refresh_from_db()
        self.assertEqual(self.child.mother_id, renamed.pk)
        self.assertEqual(self.child.mother_external_id, 'Recorded mother')
        renamed.delete()
        self.child.refresh_from_db()
        self.assertIsNone(self.child.mother_id)
        self.assertEqual(self.child.mother_external_id, 'Recorded mother')

    def test_editor_resolves_exact_ids_and_clears_both_values(self):
        url = reverse('lab:edit_selected_mice_all')
        for extra, expected in [
            ({'mother_external_id': self.mother.pk}, self.mother.pk),
            ({'mother_external_id': self.mother.pk, 'mother': self.mother.pk}, self.mother.pk),
            ({'mother_external_id': self.mother.pk, 'mother': ''}, self.mother.pk),
            ({'mother_external_id': '', 'mother': ''}, None),
        ]:
            response = self.client.post(url, self.payload(update_mother='on', **extra))
            self.assertEqual(response.status_code, 302, response.context['form'].errors if response.status_code == 200 else '')
            self.child.refresh_from_db()
            self.assertEqual(self.child.mother_id, expected)
            self.assertEqual(self.child.mother_external_id, extra['mother_external_id'])

    def test_changed_text_discards_stale_hidden_reference_without_js(self):
        self.link()
        response = self.client.post(reverse('lab:edit_selected_mice_all'), self.payload(
            update_mother='on', mother=self.mother.pk, mother_external_id='A different external parent'))
        self.assertEqual(response.status_code, 302)
        self.child.refresh_from_db()
        self.assertIsNone(self.child.mother_id)

    def test_unchecked_parent_fields_leave_links_unchanged(self):
        self.link()
        response = self.client.post(reverse('lab:edit_selected_mice_all'), self.payload(update_status='on', status='Culled', mother_external_id='accident'))
        self.assertEqual(response.status_code, 302)
        self.child.refresh_from_db()
        self.assertEqual(self.child.mother_id, self.mother.pk)

    def test_shared_parents_and_individual_override(self):
        form = BulkMouseUpdateForm(selected_mice=[self.child, self.father])
        override = form.individual_parent_controls[1]['controls'][0]
        data = {**self.payload(), 'parent_group_version': form.initial['parent_group_version'], 'selected_mouse': [self.child.pk, self.father.pk],
                individual_mouse_field_prefix(self.father) + '_mouse_id': self.father.pk,
                'update_mother': 'on', 'mother_external_id': self.mother.pk, 'mother': self.mother.pk,
                override['checkbox'].name: 'on', override['text'].name: 'External mum', override['reference'].name: ''}
        bound = BulkMouseUpdateForm(data, selected_mice=[self.child, self.father])
        self.assertTrue(bound.is_valid(), bound.errors)
        apply_bulk_mouse_updates([self.child, self.father], bound)
        self.child.refresh_from_db(); self.father.refresh_from_db()
        self.assertEqual(self.child.mother_id, self.mother.pk)
        self.assertIsNone(self.father.mother_id)
        self.assertEqual(self.father.mother_external_id, 'External mum')

    def test_invalid_batch_rolls_back_other_edits(self):
        form = BulkMouseUpdateForm(self.payload(update_mother='on', mother=self.child.pk, mother_external_id=self.child.pk,
                                  update_status='on', status='Culled'), selected_mice=[self.child])
        self.assertFalse(form.is_valid())
        self.child.refresh_from_db()
        self.assertEqual(self.child.status, 'Alive')

    def test_creation_accepts_shared_linked_and_external_parents(self):
        species = Species.objects.get(name='Mouse')
        line = MouseLine.objects.create(name='Family line', species=species)
        response = self.client.post(reverse('lab:add_litter_all'), {
            'mouse_id_prefix': 'New child', 'mouse_numbers': '', 'protocol': self.protocol.pk,
            'crossing_definition': line.crossings.get().pk, 'crossing_search': line.name,
            'status': 'Alive', 'pup__sex': 'F', 'mother': self.mother.pk,
            'mother_external_id': self.mother.pk, 'father_external_id': 'Labtrack dad'})
        self.assertEqual(response.status_code, 302, response.context['form'].errors if response.status_code == 200 else '')
        child = Mouse.objects.get(pk='New child')
        self.assertEqual(child.mother_id, self.mother.pk)
        self.assertEqual(child.father_external_id, 'Labtrack dad')

    def test_family_groups_preview_limit_order_and_query_count(self):
        self.link(father_id=self.father.pk, father_external_id=self.father.pk)
        Mouse.objects.bulk_create([Mouse(mouse_id=f'Sibling-{i}', protocol=self.protocol, mother=self.mother,
            father=self.father, date_of_birth=date(2026, 2, i+1)) for i in range(7)])
        Mouse.objects.create(mouse_id='External dad child', protocol=self.protocol, mother=self.mother, father_external_id='LT-1')
        with self.assertNumQueries(4):
            sections = build_family_sections([self.mother, self.child])
        family = sections[0]
        self.assertEqual(family['offspring_count'], 9)
        self.assertEqual(len(family['groups']), 2)
        self.assertEqual(family['groups'][0]['count'], 8)
        self.assertEqual(len(family['groups'][0]['preview']), 5)
        self.assertEqual(family['groups'][0]['preview'][0]['id'], self.child.pk)

    def test_parent_and_offspring_combined_summaries_cross_protocol(self):
        self.link(father_id=self.father.pk, father_external_id=self.father.pk)
        for relation, mouse, count in [('parents', self.child, 2), ('offspring', self.mother, 1)]:
            response = self.client.get(reverse('lab:animal_relatives', args=[mouse.pk]), {'relation': relation})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.context['selected_mice']), count)
        response = self.client.get(reverse('lab:view_selected_mice_all'), {'selected_mouse': [self.mother.pk, self.father.pk], 'mouse_editor_tab': 'family'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Offspring recorded in this ELN')

    def test_summary_records_original_identifier_after_rename(self):
        self.link()
        response = self.client.get(reverse('lab:animal_summary', args=[self.child.pk]), {'mouse_editor_tab': 'family'})
        self.assertContains(response, 'Recorded ID: Recorded mother')
        self.assertContains(response, reverse('lab:animal_summary', args=[self.mother.pk]))

    def test_reconciliation_selection_staleness_repeat_and_no_overwrite(self):
        apply_parentage({self.child.pk: {'mother_external_id': '  mother / 001  '}})
        state = graph_state(); candidates = matching_candidates(self.mother.pk, state)
        token = reconciliation_fingerprint(self.mother.pk, candidates, state)
        reconcile(self.mother.pk, [candidates[0]['key']], token)
        self.child.refresh_from_db()
        self.assertEqual(self.child.mother_id, self.mother.pk)
        self.assertEqual(self.child.mother_external_id, '  mother / 001  ')
        with self.assertRaisesMessage(ValidationError, 'changed'):
            reconcile(self.mother.pk, [candidates[0]['key']], token)
        self.assertEqual(matching_candidates(self.mother.pk), [])

    def test_reconciliation_ambiguous_and_invalid_candidates(self):
        apply_parentage({self.child.pk: {'mother_external_id': self.mother.pk}})
        Mouse.objects.create(mouse_id=self.mother.pk.lower(), protocol=self.protocol)
        self.assertFalse(matching_candidates(self.mother.pk)[0]['eligible'])
        apply_parentage({self.mother.pk: {'mother_external_id': self.mother.pk}})
        self.assertTrue(all(not c['eligible'] for c in matching_candidates(self.mother.pk)))

    def test_retired_per_animal_resolution_redirects_to_maintenance_and_cannot_save(self):
        apply_parentage({self.child.pk: {'mother_external_id': self.mother.pk}})
        for route in ('parentage_candidates', 'reconcile_parentage'):
            url = reverse('lab:' + route, args=[self.mother.pk])
            self.assertRedirects(self.client.get(url), reverse('lab:maintenance'), fetch_redirect_response=False)
            self.assertEqual(self.client.post(url, {}).status_code, 405)
        self.child.refresh_from_db()
        self.assertIsNone(self.child.mother_id)

    def test_lookup_excludes_current_mouse_and_retains_response_shape(self):
        response = self.client.get(reverse('lab:mouse_lookup'), {'q': 'Child', 'exclude': self.child.pk})
        self.assertEqual(response.json(), {'results': []})

    def test_unenhanced_parent_text_controls_are_not_html_disabled(self):
        form = BulkMouseUpdateForm(selected_mice=[self.child])
        self.assertNotIn('disabled', str(form['mother_external_id']))
        self.assertNotIn('disabled', str(form['father_external_id']))

    def test_model_full_clean_rejects_invalid_parentage(self):
        self.child.mother_id = self.child.pk
        with self.assertRaises(ValidationError):
            self.child.full_clean()

    def test_service_records_identifier_when_linking_an_empty_slot(self):
        apply_parentage({self.child.pk: {'father_id': self.father.pk}})
        self.child.refresh_from_db()
        self.assertEqual(self.child.father_external_id, self.father.pk)

    def test_swapping_parent_ids_moves_both_links_and_preserves_recorded_ids(self):
        self.link(father_id=self.father.pk, father_external_id=self.father.pk)
        old_mother, old_father = self.mother.pk, self.father.pk
        data = {'selected_mouse': [old_mother, old_father], 'confirm_mouse_ids': 'on',
                individual_mouse_field_prefix(self.mother) + '_mouse_id': old_father,
                individual_mouse_field_prefix(self.father) + '_mouse_id': old_mother}
        form = BulkMouseUpdateForm(data, selected_mice=[self.mother, self.father])
        self.assertTrue(form.is_valid(), form.errors)
        apply_bulk_mouse_updates([self.mother, self.father], form)
        self.child.refresh_from_db()
        self.assertEqual((self.child.mother_id, self.child.father_id), (old_father, old_mother))
        self.assertEqual((self.child.mother_external_id, self.child.father_external_id), ('Recorded mother', old_father))

    def test_rename_failure_rolls_back_parent_changes_in_the_same_transaction(self):
        data = self.payload(update_mother='on', mother=self.mother.pk, mother_external_id=self.mother.pk,
                            confirm_mouse_ids='on', **{individual_mouse_field_prefix(self.child) + '_mouse_id': 'New child ID'})
        form = BulkMouseUpdateForm(data, selected_mice=[self.child])
        self.assertTrue(form.is_valid(), form.errors)
        with patch('lab.views.mice.rename_mouse_identity', side_effect=IntegrityError('Concurrent conflict')):
            with self.assertRaises(IntegrityError):
                apply_bulk_mouse_updates([self.child], form)
        self.child.refresh_from_db()
        self.assertIsNone(self.child.mother_id)
        self.assertFalse(Mouse.objects.filter(pk='New child ID').exists())

    def test_reconciliation_can_link_only_selected_candidates(self):
        sibling = Mouse.objects.create(mouse_id='Unselected sibling', protocol=self.protocol, mother_external_id=self.mother.pk)
        apply_parentage({self.child.pk: {'mother_external_id': self.mother.pk}})
        state = graph_state(); candidates = matching_candidates(self.mother.pk, state)
        selected = next(c['key'] for c in candidates if c['mouse_id'] == self.child.pk)
        reconcile(self.mother.pk, [selected], reconciliation_fingerprint(self.mother.pk, candidates, state))
        sibling.refresh_from_db(); self.child.refresh_from_db()
        self.assertIsNone(sibling.mother_id)
        self.assertEqual(self.child.mother_id, self.mother.pk)
