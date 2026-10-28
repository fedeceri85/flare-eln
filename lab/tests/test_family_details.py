"""Family tab placement, parent grouping, exact matching and safe staged edits."""
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from ..family import build_family_sections, build_parent_groups
from ..forms import BulkMouseUpdateForm, individual_mouse_field_prefix
from ..models import Mouse, Protocol
from ..parentage import apply_parentage
from ..test_navigation import Markup
from ..views.mice import apply_bulk_mouse_updates


@override_settings(ALLOWED_HOSTS=['testserver'])
class FamilyDetailsTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username='family-details'))
        self.protocol = Protocol.objects.create(protocol_number=885, licence_reference='FAMILY-TAB', name='Family tabs')
        self.mum = Mouse.objects.create(mouse_id='Mother', protocol=self.protocol, sex='F')
        self.dad = Mouse.objects.create(mouse_id='Father', protocol=self.protocol, sex='M')
        self.other = Mouse.objects.create(mouse_id='Other mother', protocol=self.protocol, sex='F')
        self.children = [Mouse.objects.create(mouse_id=f'Child-{i}', protocol=self.protocol, mother=self.mum,
                         mother_external_id=f'Original mother {i}', father=self.dad, father_external_id=self.dad.pk) for i in range(2)]
        self.third = Mouse.objects.create(mouse_id='Different parents', protocol=self.protocol, mother=self.other,
                                         mother_external_id=self.other.pk, father_external_id='LT dad')

    def payload(self, mice, **extra):
        form = BulkMouseUpdateForm(selected_mice=mice)
        return {'selected_mouse': [mouse.pk for mouse in mice], 'apply_bulk_edit': '1', 'mouse_editor_tab': 'family',
                **{individual_mouse_field_prefix(mouse) + '_mouse_id': mouse.pk for mouse in mice},
                **({'parent_group_version': form.initial['parent_group_version']} if len(mice) > 1 else {}), **extra}

    def test_tab_order_and_parent_controls_are_in_family_on_add_and_edit(self):
        for route, params in [('add_litter_all', {}), ('edit_selected_mice_all', {'selected_mouse': [self.children[0].pk]})]:
            response = self.client.get(reverse('lab:' + route), {**params, 'mouse_editor_tab': 'family'})
            markup = Markup(response.content.decode())
            names = [node.attrs['data-mouse-tab'] for node in markup.find('button', role='tab')]
            self.assertEqual([name for name in names if name != 'individual'][:3], ['ids', 'shared', 'family'])
            for node in markup.find('input', **{'data-parent-text': None}):
                self.assertTrue(node.within('section', id='mouse-panel-family'))
                self.assertFalse(node.within('section', id='mouse-panel-shared'))
            self.assertNotContains(response, 'Offspring recorded in this ELN')
            self.assertNotContains(response, 'data-parent-clear')
            self.assertNotContains(response, 'data-parent-unlink')
            self.assertContains(response, 'Remove parent')
            self.assertEqual(markup.find('input', name='mouse_editor_tab')[0].attrs['value'], 'family')

    def test_summary_groups_linked_parents_and_keeps_recorded_provenance(self):
        selected = [*self.children, self.third]
        with self.assertNumQueries(4):
            families = build_family_sections(selected)
            groups = build_parent_groups(selected, families=families)
        self.assertEqual([g['count'] for g in groups], [2, 1])
        self.assertEqual([m['id'] for m in groups[0]['members']], [m.pk for m in self.children])
        self.assertEqual([r['value'] for r in groups[0]['parents'][0]['recorded_ids']], ['Original mother 0', 'Original mother 1'])
        response = self.client.get(reverse('lab:view_selected_mice_all'), {'selected_mouse': [m.pk for m in selected], 'mouse_editor_tab': 'family'})
        self.assertEqual(response.context['active_tab'], 'family')
        markup = Markup(response.content.decode())
        self.assertTrue(markup.find('h2', id='family-parents-title')[0].within('section', id='mouse-panel-family'))
        self.assertTrue(markup.find('h2', id='family-offspring-title')[0].within('section', id='mouse-panel-family'))
        self.assertContains(response, 'Recorded IDs differ')
        edit = markup.find('a', **{'data-mouse-summary-edit': None})[0].attrs['href']
        self.assertEqual(parse_qs(urlsplit(edit).query)['mouse_editor_tab'], ['individual'])

    def test_view_all_offspring_is_a_link_next_to_its_count(self):
        response = self.client.get(reverse('lab:animal_summary', args=[self.mum.pk]), {'mouse_editor_tab': 'family'})
        markup = Markup(response.content.decode())
        link = next(node for node in markup.find('a') if node.text == 'View all offspring')
        self.assertNotIn('button', link.attrs.get('class', ''))
        self.assertIn('Offspring recorded in this ELN', link.parent.text)

    def test_each_parent_group_edits_only_its_members_with_individual_override(self):
        mice = [*self.children, self.third]
        form = BulkMouseUpdateForm(selected_mice=mice)
        self.assertEqual([g['count'] for g in form.parent_groups], [2, 1])
        group = form.parent_groups[0]; mother = group['controls'][0]
        override = group['overrides'][1]['controls'][0]
        data = self.payload(mice, **{mother['checkbox'].name: 'on', mother['text'].name: self.other.pk,
                                     override['checkbox'].name: 'on', override['text'].name: 'External override'})
        bound = BulkMouseUpdateForm(data, selected_mice=mice)
        self.assertTrue(bound.is_valid(), bound.errors)
        apply_bulk_mouse_updates(mice, bound)
        for mouse in mice: mouse.refresh_from_db()
        self.assertEqual(self.children[0].mother_id, self.other.pk)
        self.assertIsNone(self.children[1].mother_id)
        self.assertEqual(self.children[1].mother_external_id, 'External override')
        self.assertEqual(self.third.mother_id, self.other.pk)
        self.assertEqual(self.third.father_external_id, 'LT dad')

    def test_unchanged_group_parent_preserves_each_original_identifier(self):
        form = BulkMouseUpdateForm(self.payload(self.children, update_mother='on', mother=self.mum.pk,
                                   mother_external_id=self.mum.pk), selected_mice=self.children)
        self.assertTrue(form.is_valid(), form.errors)
        apply_bulk_mouse_updates(self.children, form)
        for i, mouse in enumerate(self.children):
            mouse.refresh_from_db(); self.assertEqual(mouse.mother_external_id, f'Original mother {i}')

    def test_cleared_text_overrides_stale_even_nonexistent_hidden_reference(self):
        child = self.children[0]
        form = BulkMouseUpdateForm(self.payload([child], update_mother='on', mother_external_id='', mother='Deleted parent'), selected_mice=[child])
        self.assertTrue(form.is_valid(), form.errors)
        apply_bulk_mouse_updates([child], form); child.refresh_from_db()
        self.assertIsNone(child.mother_id); self.assertEqual(child.mother_external_id, '')

    def test_save_resolves_exact_ids_without_js_or_a_finished_lookup(self):
        child = self.children[0]
        data = self.payload([child], update_mother='on', mother_external_id='  OTHER MOTHER  ', mother=self.mum.pk)
        response = self.client.post(reverse('lab:edit_selected_mice_all'), data)
        self.assertEqual(response.status_code, 302)
        child.refresh_from_db(); self.assertEqual(child.mother_id, self.other.pk)
        self.assertEqual(child.mother_external_id, 'OTHER MOTHER')

    def test_ambiguous_id_stays_external_until_explicit_selection(self):
        Mouse.objects.create(mouse_id='MOTHER', protocol=self.protocol)
        child = self.children[0]
        data = self.payload([child], update_mother='on', mother_external_id='mother', mother='')
        form = BulkMouseUpdateForm(data, selected_mice=[child])
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data['parentage_updates'][child.pk]['mother_id'])
        self.assertTrue(form.parentage_resolution_warnings)
        data.update(mother='Mother', mother_external_id='Mother')
        form = BulkMouseUpdateForm(data, selected_mice=[child]); self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['parentage_updates'][child.pk]['mother_id'], 'Mother')

    def test_lookup_exact_match_is_independent_of_suggestion_limit_and_opt_in(self):
        Mouse.objects.bulk_create([Mouse(mouse_id=f'A-{i}-ZZZ', protocol=self.protocol) for i in range(12)])
        target = Mouse.objects.create(mouse_id='ZZZ', protocol=self.protocol)
        response = self.client.get(reverse('lab:mouse_lookup'), {'q': 'ZZZ', 'resolve_exact': '1'}).json()
        self.assertEqual(len(response['results']), 10)
        self.assertEqual(response['exact_match']['id'], target.pk)
        self.assertFalse(response['ambiguous'])
        generic = self.client.get(reverse('lab:mouse_lookup'), {'q': 'ZZZ'}).json()
        self.assertEqual(set(generic), {'results'})

    def test_group_membership_change_rejects_stale_edits(self):
        data = self.payload(self.children, update_mother='on', mother_external_id='External')
        apply_parentage({self.children[1].pk: {'mother_id': self.other.pk}})
        response = self.client.post(reverse('lab:edit_selected_mice_all'), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Parent groups changed.')
        self.children[0].refresh_from_db(); self.assertEqual(self.children[0].mother_id, self.mum.pk)

    def test_membership_is_rechecked_inside_the_save_transaction(self):
        form = BulkMouseUpdateForm(self.payload(self.children, update_mother='on', mother_external_id='External'), selected_mice=self.children)
        self.assertTrue(form.is_valid(), form.errors)
        apply_parentage({self.children[1].pk: {'mother_id': self.other.pk}})
        with self.assertRaisesMessage(ValidationError, 'Parent groups changed'):
            apply_bulk_mouse_updates(self.children, form)

    def test_self_link_validation_errors_are_present_in_family_panel(self):
        child = self.children[0]
        response = self.client.post(reverse('lab:edit_selected_mice_all'), self.payload([child], update_mother='on', mother_external_id=child.pk))
        self.assertEqual(response.status_code, 200)
        markup = Markup(response.content.decode())
        errors = [node for node in markup.find('ul', **{'class': 'error-list'}) if 'own parent' in node.text]
        self.assertTrue(any(node.within('section', id='mouse-panel-family') for node in errors))

    def test_parent_links_open_genotypes_with_single_parent_fallback(self):
        response = self.client.get(reverse('lab:view_selected_mice_all'), {'selected_mouse': [mouse.pk for mouse in self.children], 'mouse_editor_tab': 'family'})
        url = response.context['parent_groups'][0]['parents_url']
        self.assertEqual(parse_qs(urlsplit(url).query)['mouse_editor_tab'], ['individual'])
        parents = self.client.get(url)
        self.assertEqual(parents.context['active_tab'], 'individual')
        response = self.client.get(reverse('lab:animal_summary', args=[self.third.pk]), {'mouse_editor_tab': 'family'})
        parent = self.client.get(response.context['parent_groups'][0]['parents_url'])
        self.assertEqual(parent.context['active_tab'], 'shared')

    def test_fresh_summary_and_editor_start_on_genotypes_for_multiple_animals(self):
        params = {'selected_mouse': [mouse.pk for mouse in self.children]}
        response = self.client.get(reverse('lab:view_selected_mice_all'), params)
        self.assertEqual(response.context['active_tab'], 'individual')
        editor = self.client.get(reverse('lab:edit_selected_mice_all'), params)
        markup = Markup(editor.content.decode())
        self.assertEqual(markup.find('button', role='tab')[0].attrs['data-mouse-tab'], 'individual')
        self.assertEqual(markup.find('input', name='mouse_editor_tab')[0].attrs['value'], 'individual')
        editor = self.client.get(reverse('lab:edit_selected_mice_all'), {'selected_mouse': self.children[0].pk})
        self.assertEqual(Markup(editor.content.decode()).find('input', name='mouse_editor_tab')[0].attrs['value'], 'shared')
