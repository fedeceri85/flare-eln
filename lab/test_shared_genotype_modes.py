"""Shared per-line results must agree between previews, forms and saved animals."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import genotype_field_name, individual_mouse_field_prefix, sync_mouse_structured_genetics
from .models import Mouse, MouseLine, Protocol, Species, resolve_crossing
from .test_navigation import Markup


@override_settings(ALLOWED_HOSTS=['testserver'])
class SharedGenotypeModeTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username='genotype-modes'))
        self.protocol = Protocol.objects.create(protocol_number=188, licence_reference='MODES', name='Modes')
        species = Species.objects.create(name='Mode species')
        self.lines = [MouseLine.objects.create(species=species, name=name) for name in ('Mode A', 'Mode B')]
        self.crossing, _ = resolve_crossing(species, self.lines)
        self.mice = [Mouse.objects.create(mouse_id=f'Mode-{i}', protocol=self.protocol) for i in (1, 2)]
        for mouse, result in zip(self.mice, ('het', 'wt')):
            sync_mouse_structured_genetics(mouse, self.crossing, {line.pk: result for line in self.lines})
        self.a, self.b = map(genotype_field_name, self.lines)

    def edit_data(self):
        return {'apply_bulk_edit': '1', 'selected_mouse': [mouse.pk for mouse in self.mice], 'genotype_modes_present': '1',
                'crossing_definition': self.crossing.pk,
                self.a: 'hom', self.b: 'hom', self.b + '_individual': 'on',
                **{f'{individual_mouse_field_prefix(mouse)}_{self.b}': result
                   for mouse, result in zip(self.mice, ('het', 'wt'))}}

    def test_shared_line_saves_without_individual_inputs_and_other_line_stays_individual(self):
        response = self.client.post(reverse('lab:edit_selected_mice_all'), self.edit_data())
        self.assertEqual(response.status_code, 302)
        for mouse, result in zip(self.mice, ('het', 'wt')):
            self.assertEqual(mouse.genotype_calls.get(mouse_line=self.lines[0]).zygosity, 'hom')
            self.assertEqual(mouse.genotype_calls.get(mouse_line=self.lines[1]).zygosity, result)

    def test_validation_and_ajax_preview_preserve_modes(self):
        data = self.edit_data()
        data['update_protocol'] = 'on'
        response = self.client.post(reverse('lab:edit_selected_mice_all'), data)
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertFalse(form[self.a + '_individual'].value())
        self.assertTrue(form[self.b + '_individual'].value())
        data['preview_individual_mice'] = '1'
        preview = self.client.post(reverse('lab:edit_selected_mice_all'), data, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(preview.status_code, 200)
        self.assertIn('html', preview.json())
        self.assertEqual(self.mice[0].genotype_calls.get(mouse_line=self.lines[0]).zygosity, 'het')

    def test_litter_shared_result_overrides_stale_row_values_for_only_that_line(self):
        data = {'mouse_id_prefix': 'New-mode-', 'mouse_numbers': '1-2', 'protocol': self.protocol.pk,
                'crossing_definition': self.crossing.pk, 'genotype_modes_present': '1',
                self.a: 'hom', self.b: 'hom', self.b + '_individual': 'on',
                f'pup_1_{self.a}': 'wt', f'pup_2_{self.a}': 'het',
                f'pup_1_{self.b}': 'het', f'pup_2_{self.b}': 'wt'}
        response = self.client.post(reverse('lab:add_litter_all'), data)
        self.assertEqual(response.status_code, 302)
        for i, result in zip((1, 2), ('het', 'wt')):
            mouse = Mouse.objects.get(pk=f'New-mode-{i}')
            self.assertEqual(mouse.genotype_calls.get(mouse_line=self.lines[0]).zygosity, 'hom')
            self.assertEqual(mouse.genotype_calls.get(mouse_line=self.lines[1]).zygosity, result)

    def test_crossing_is_at_top_of_genotype_panel_and_tab_precedes_animal_details(self):
        selection = {'selected_mouse': [mouse.pk for mouse in self.mice]}
        for name in ('edit_selected_mice_all', 'view_selected_mice_all'):
            response = self.client.get(reverse('lab:' + name), selection)
            markup = Markup(response.content.decode())
            tabs = [node.attrs['data-mouse-tab'] for node in markup.find('button', role='tab')]
            self.assertLess(tabs.index('individual'), tabs.index('shared'))
            if name.startswith('edit'):
                crossing = markup.find('input', name='crossing_search')[0]
                self.assertTrue(crossing.within('section', id='mouse-panel-individual'))
                self.assertTrue(markup.find('input', name=self.a + '_individual'))
            else:
                shared = markup.find('section', id='mouse-panel-shared')[0]
                self.assertNotIn(self.crossing.display_name, shared.text)

    def test_new_crossing_preview_defaults_new_lines_to_individual_selection(self):
        response = self.client.post(reverse('lab:add_litter_all'), {
            'mouse_id_prefix': 'Preview-mode-', 'mouse_numbers': '1-2', 'protocol': self.protocol.pk,
            'crossing_definition': self.crossing.pk, 'genotype_modes_present': '1', 'preview_mice': '1',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        markup = Markup(response.json()['genotype_html'])
        for name in (self.a, self.b):
            self.assertIn('checked', markup.find('input', name=name + '_individual')[0].attrs)

    def test_no_javascript_apply_preserves_individually_selected_lines(self):
        response = self.client.post(reverse('lab:add_litter_all'), {
            'mouse_id_prefix': 'Preview-mode-', 'mouse_numbers': '1-2', 'protocol': self.protocol.pk,
            'crossing_definition': self.crossing.pk, 'genotype_modes_present': '1', 'apply_shared_genotype': '1',
            self.a: 'hom', self.b: 'hom', self.b + '_individual': 'on',
            f'pup_1_{self.b}': 'wt', f'pup_2_{self.b}': 'het',
        })
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertEqual(form[f'pup_1_{self.a}'].value(), 'hom')
        self.assertEqual(form[f'pup_1_{self.b}'].value(), 'wt')
        self.assertEqual(form[f'pup_2_{self.b}'].value(), 'het')
