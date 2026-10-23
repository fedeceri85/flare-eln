from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, Client, override_settings
from django.urls import reverse

from ..genetics_catalogue import rename_line, reorder_crossing
from ..models import Crossing, CrossingLine, Mouse, MouseGenotype, MouseLine, Protocol, Species, resolve_crossing


@override_settings(ALLOWED_HOSTS=['testserver'])
class GeneticsCatalogueTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_user(username='catalogue-staff', is_staff=True)
        cls.species = Species.objects.get(name='Mouse')
        cls.a = MouseLine.objects.create(species=cls.species, name='Alpha')
        cls.b = MouseLine.objects.create(species=cls.species, name='beta')
        cls.crossing, _ = resolve_crossing(cls.species, [cls.a, cls.b])
        protocol = Protocol.objects.create(protocol_number=301, licence_reference='TEST', name='Test')
        cls.mouse = Mouse.objects.create(mouse_id='KEEP-ID', protocol=protocol, crossing_definition=cls.crossing, crossing='legacy crossing', genotype='legacy genotype')
        MouseGenotype.objects.create(mouse=cls.mouse, mouse_line=cls.a, zygosity='het', is_uncertain=True)
        MouseGenotype.objects.create(mouse=cls.mouse, mouse_line=cls.b, zygosity='wt')
        cls.url = reverse('lab:lines_crossings_page')

    def setUp(self):
        self.client.force_login(self.staff)

    def snapshot(self):
        return {
            'mice': list(Mouse.objects.order_by('pk').values()),
            'genotypes': list(MouseGenotype.objects.order_by('pk').values()),
            'members': list(CrossingLine.objects.order_by('pk').values()),
            'crossings': list(Crossing.objects.order_by('pk').values()),
            'lines': list(MouseLine.objects.order_by('pk').values()),
        }

    def test_layout_filters_tabs_and_forms_follow_results(self):
        response = self.client.get(self.url)
        html = response.content.decode()
        self.assertLess(html.index('catalogue-filter'), html.index('aria-label="Lines and crossings"'))
        self.assertLess(html.index('id="catalogue-results"'), html.index('id="add-lines"'))
        self.assertContains(response, 'filter-form--compact')
        self.assertNotContains(response, 'data-filter-autocomplete-input')
        self.assertNotContains(response, 'id="add-lines" open')
        self.assertNotContains(response, 'id="create-crossing" open')
        self.assertContains(response, 'value="75"')

    def test_live_filter_controls_and_compact_crossing_columns(self):
        response = self.client.get(self.url, {'tab': 'crossings'})
        self.assertContains(response, 'data-catalogue-filter')
        self.assertContains(response, 'data-catalogue-results-region')
        self.assertNotContains(response, 'Apply filters')
        self.assertNotContains(response, 'Clear filters')
        self.assertNotContains(response, 'Single line')
        self.assertNotContains(response, '<th scope="col">Lines</th>', html=True)
        self.assertContains(response, 'catalogue-row-action--danger')
        self.assertContains(response, 'Edit order')
        self.assertContains(response, 'Alpha x beta')

    def test_partial_case_insensitive_search_applies_to_both_tabs(self):
        for tab in ('lines', 'crossings'):
            response = self.client.get(self.url, {'q': 'LPH', 'tab': tab})
            self.assertEqual(response.context['catalogue_page'].paginator.count, 1 if tab == 'lines' else 2)
            self.assertContains(response, 'Alpha')
        self.assertContains(self.client.get(self.url, {'q': 'nothingmatches'}), 'No lines match these filters')

    def test_alphabetical_case_insensitive_order(self):
        MouseLine.objects.create(species=self.species, name='aardvark')
        MouseLine.objects.create(species=self.species, name='Zebra')
        for tab, field in (('lines', 'name'), ('crossings', 'display_name')):
            page = self.client.get(self.url, {'tab': tab}).context['catalogue_page']
            names = [getattr(item, field) for item in page]
            self.assertEqual(names, sorted(names, key=str.lower))

    def test_pagination_and_preserved_filters(self):
        MouseLine.objects.bulk_create([MouseLine(species=self.species, name=f'Extra {i:03}') for i in range(105)])
        response = self.client.get(self.url, {'q': 'Extra', 'per_page': 50, 'page': 2})
        self.assertEqual(len(response.context['catalogue_page']), 50)
        self.assertEqual(response.context['catalogue_page'].paginator.count, 105)
        params = parse_qs(urlsplit(response.context['tabs'][1]['url']).query)
        self.assertEqual(params['q'], ['Extra'])
        self.assertEqual(params['tab'], ['crossings'])
        self.assertEqual(params['per_page'], ['50'])
        self.assertNotIn('page', params)
        for size in (25, 50, 75, 100):
            self.assertEqual(self.client.get(self.url, {'per_page': size}).context['page_size'], size)
        for bad in ('oops', 150, 0):
            self.assertEqual(self.client.get(self.url, {'per_page': bad}).context['page_size'], 25)
        self.assertEqual(self.client.get(self.url, {'page': 'bad'}).context['catalogue_page'].number, 1)

    def test_species_filter_and_empty_search(self):
        rat = Species.objects.create(name='Rat')
        MouseLine.objects.create(species=rat, name='Alpha rat')
        response = self.client.get(self.url, {'species': rat.pk, 'q': 'Alpha', 'tab': 'crossings'})
        self.assertEqual(response.context['catalogue_page'].paginator.count, 1)
        cleared = self.client.get(self.url, {'species': rat.pk, 'q': '', 'tab': 'crossings'})
        self.assertEqual(cleared.context['catalogue_page'].paginator.count, 1)
        self.assertEqual(cleared.context['selected_species'], rat)
        self.assertEqual(cleared.context['active_tab'], 'crossings')
        self.assertEqual(self.client.get(self.url, {'species': 'invalid'}).status_code, 200)

    def test_rename_updates_all_crossing_names_without_touching_genetics_or_ids(self):
        before = self.snapshot()
        response = self.client.post(self.url + '?q=Alpha&per_page=50', {'action': 'rename_line', 'line_id': self.a.pk, 'name': 'Renamed', 'species': 99999, 'is_wild_type': '1'})
        self.assertEqual(response.status_code, 302)
        after = self.snapshot()
        for key in ('mice', 'genotypes', 'members'):
            self.assertEqual(before[key], after[key])
        self.a.refresh_from_db()
        self.assertEqual(self.a.name, 'Renamed')
        self.assertFalse(self.a.is_wild_type)
        self.assertEqual(self.a.species_id, self.species.pk)
        self.assertEqual(len(before['crossings']), len(after['crossings']))
        self.crossing.refresh_from_db()
        self.assertEqual(self.crossing.display_name, 'Renamed x beta')
        self.assertEqual(Crossing.objects.get(canonical_key=str(self.a.pk)).display_name, 'Renamed')
        self.mouse.refresh_from_db()
        self.assertIn('Renamed', self.mouse.genetics_genotype_display)
        self.assertEqual(parse_qs(urlsplit(response.url).query)['q'], ['Alpha'])

    def test_rename_does_not_create_missing_single_crossing(self):
        line = MouseLine.objects.bulk_create([MouseLine(species=self.species, name='No single')])[0]
        count = Crossing.objects.count()
        with patch('lab.models.ensure_single_line_crossing', side_effect=AssertionError('Save hook called')):
            rename_line(line, 'Still no single')
        self.assertEqual(Crossing.objects.count(), count)

    def test_duplicate_and_blank_names_rejected_without_changes(self):
        before = self.snapshot()
        for name in ('BETA', '', 'X' * 201):
            response = self.client.post(self.url, {'action': 'rename_line', 'line_id': self.a.pk, 'name': name})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['editor_form'].errors)
            self.assertEqual(self.snapshot(), before)

    def test_rename_overflow_rolls_back_every_affected_label(self):
        lines = [MouseLine.objects.create(species=self.species, name=letter * 150) for letter in ('x', 'y')]
        resolve_crossing(self.species, [self.a, self.b, *lines])
        before = self.snapshot()
        with self.assertRaises(ValidationError):
            rename_line(self.a, 'z' * 200)
        self.assertEqual(self.snapshot(), before)

    def test_reorder_used_crossing_preserves_membership_ids_and_genotype_values(self):
        before = self.snapshot()
        key = self.crossing.canonical_key
        response = self.client.post(self.url, {'action': 'reorder_crossing', 'crossing_id': self.crossing.pk, 'selected_lines': f'{self.b.pk},{self.a.pk}'})
        self.assertEqual(response.status_code, 302)
        self.crossing.refresh_from_db()
        self.assertEqual(self.crossing.display_name, 'beta x Alpha')
        self.assertEqual(self.crossing.canonical_key, key)
        after = self.snapshot()
        for field in ('mice', 'genotypes', 'lines'):
            self.assertEqual(before[field], after[field])
        self.assertEqual([(row['crossing_line_id'], row['line_id']) for row in before['members']], [(row['crossing_line_id'], row['line_id']) for row in after['members']])
        self.assertEqual([line.pk for line in self.crossing.ordered_lines()], [self.b.pk, self.a.pk])

    def test_forged_reorder_sets_rejected_atomically(self):
        other = MouseLine.objects.create(species=self.species, name='Other')
        before = self.snapshot()
        for ids in (str(self.a.pk), f'{self.a.pk},{self.a.pk}', f'{other.pk},{self.a.pk}', f'{self.a.pk},{self.b.pk},{other.pk}', 'invalid', ''):
            response = self.client.post(self.url, {'action': 'reorder_crossing', 'crossing_id': self.crossing.pk, 'selected_lines': ids})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['editor_form'].errors)
            self.assertEqual(before, self.snapshot())
        with self.assertRaises(ValidationError):
            reorder_crossing(self.crossing, [other.pk, self.a.pk])

    def test_get_editor_prepopulated_and_no_javascript_moves_only_save_on_explicit_save(self):
        response = self.client.get(self.url, {'edit_crossing': self.crossing.pk})
        self.assertEqual(response.context['edit_kind'], 'crossing')
        self.assertEqual([line.pk for line in response.context['editor_lines']], [self.a.pk, self.b.pk])
        before = self.snapshot()
        moved = self.client.post(self.url, {'action': 'reorder_crossing', 'crossing_id': self.crossing.pk, 'selected_lines': f'{self.a.pk},{self.b.pk}', 'move': f'{self.b.pk}:up'})
        self.assertEqual(moved.status_code, 200)
        self.assertEqual([line.pk for line in moved.context['editor_lines']], [self.b.pk, self.a.pk])
        self.assertEqual(before, self.snapshot())
        self.assertContains(self.client.get(self.url, {'edit_line': self.a.pk}), 'value="Alpha"')

    def test_create_existing_crossing_does_not_reorder_it(self):
        response = self.client.post(self.url, {'action': 'create_crossing', 'species': self.species.pk, 'selected_lines': f'{self.b.pk},{self.a.pk}'})
        self.assertEqual(response.status_code, 302)
        self.crossing.refresh_from_db()
        self.assertEqual(self.crossing.display_name, 'Alpha x beta')

    def test_create_errors_preserve_selection_and_open_panel(self):
        inactive = MouseLine.objects.create(species=self.species, name='Inactive', active=False)
        response = self.client.post(self.url, {'action': 'create_crossing', 'species': self.species.pk, 'selected_lines': f'{self.a.pk},{inactive.pk}'})
        self.assertContains(response, 'id="create-crossing" open')
        self.assertEqual([item['id'] for item in response.context['initial_selected_lines']], [self.a.pk, inactive.pk])
        bad = self.client.post(self.url, {'action': 'add_lines', 'species': self.species.pk, 'line_names': 'a' * 201})
        self.assertContains(bad, 'id="add-lines" open')
        self.assertTrue(bad.context['line_form'].errors)
        self.assertEqual(self.client.post(self.url, {'action': 'create_crossing', 'species': 'bad', 'selected_lines': str(self.a.pk)}).status_code, 200)

    def test_create_crossing_name_limit(self):
        long_lines = [MouseLine.objects.create(species=self.species, name=c * 180) for c in ('c', 'd', 'e')]
        before = self.snapshot()
        response = self.client.post(self.url, {'action': 'create_crossing', 'species': self.species.pk, 'selected_lines': ','.join(str(line.pk) for line in long_lines)})
        self.assertTrue(response.context['crossing_form'].errors)
        self.assertEqual(before, self.snapshot())

    def test_panels_can_be_opened_without_javascript(self):
        for panel in ('add-lines', 'create-crossing'):
            self.assertContains(self.client.get(self.url, {'panel': panel}), f'id="{panel}" open')

    def test_permissions_forged_ids_and_csrf(self):
        for value in ('bad', 2**80, 999999):
            self.assertEqual(self.client.get(self.url, {'edit_line': value}).status_code, 404)
        self.assertEqual(self.client.post(self.url, {'action': 'unknown'}).status_code, 403)
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.staff)
        self.assertEqual(secure.post(self.url, {'action': 'rename_line', 'line_id': self.a.pk, 'name': 'bad'}).status_code, 403)
        user = get_user_model().objects.create_user(username='ordinary')
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {'action': 'reorder_crossing', 'crossing_id': self.crossing.pk, 'selected_lines': f'{self.b.pk},{self.a.pk}'}).status_code, 403)
