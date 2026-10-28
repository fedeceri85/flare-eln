from datetime import date
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ..models import Mouse, Procedure, ProcedureType, Protocol


@override_settings(ALLOWED_HOSTS=['testserver'])
class ProcedureCreatorAndNotesFiltersTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = get_user_model().objects.create_user(username='AliceResearcher')
        cls.bob = get_user_model().objects.create_user(username='BobResearcher')
        cls.protocol = Protocol.objects.create(protocol_number=924, licence_reference='FILTER', name='Filter protocol')
        cls.other_protocol = Protocol.objects.create(protocol_number=925, licence_reference='FILTER', name='Other protocol')
        year = timezone.localdate().year
        cls.current = date(year, 1, 1)
        cls.a = Mouse.objects.create(mouse_id='ALICE', protocol=cls.protocol, added_by=cls.alice, notes='Needs Follow-Up', protocol_start_date=cls.current)
        cls.b = Mouse.objects.create(mouse_id='BOB', protocol=cls.protocol, added_by=cls.bob, notes='Control animal', protocol_start_date=cls.current)
        cls.unknown = Mouse.objects.create(mouse_id='UNKNOWN', protocol=cls.protocol, notes='Follow-up', protocol_start_date=cls.current)
        cls.old = Mouse.objects.create(mouse_id='OLD', protocol=cls.protocol, added_by=cls.alice, notes='Follow-up', protocol_start_date=date(year - 1, 1, 1))
        Mouse.objects.create(mouse_id='OTHER-PROTOCOL', protocol=cls.other_protocol, added_by=cls.alice, notes='Follow-up', protocol_start_date=cls.current)
        kind = ProcedureType.objects.create(name='Filter test')
        Procedure.objects.create(mouse=cls.b, procedure_type=kind, date=cls.current, notes='Follow-up procedure notes')
        cls.url = reverse('lab:procedure_page_licence_protocol', args=['FILTER', 924])

    def setUp(self):
        self.client.force_login(self.alice)

    def mouse_ids(self, response):
        return set(response.context['page_obj'].object_list.values_list('pk', flat=True)) if 'page_obj' in response.context else {
            row['cells'][0]['checkbox']['value'] for row in response.context['table']['rows']
        }

    def test_added_by_partial_case_insensitive_matches_current_account(self):
        response = self.client.get(self.url, {'added_by': 'LICEres'})
        self.assertEqual(self.mouse_ids(response), {'ALICE', 'OLD'})
        self.alice.username = 'RenamedAccount'
        self.alice.save(update_fields=['username'])
        self.assertEqual(self.mouse_ids(self.client.get(self.url, {'added_by': 'renamed'})), {'ALICE', 'OLD'})

    def test_notes_matches_mouse_notes_not_procedure_notes(self):
        self.assertEqual(self.mouse_ids(self.client.get(self.url, {'notes': '  FOLLOW  '})), {'ALICE', 'UNKNOWN', 'OLD'})

    def test_filters_combine_and_respect_protocol_across_years(self):
        params = {'added_by': 'alice', 'notes': 'follow'}
        self.assertEqual(self.mouse_ids(self.client.get(self.url, params)), {'ALICE', 'OLD'})
        params['show_current_year'] = '1'
        self.assertEqual(self.mouse_ids(self.client.get(self.url, params)), {'ALICE'})
        params['notes'] = 'Control'
        self.assertEqual(self.mouse_ids(self.client.get(self.url, params)), set())

    def test_empty_filters_include_unknown_creators_and_inputs_preserve_values(self):
        response = self.client.get(self.url, {'added_by': '', 'notes': ''})
        self.assertEqual(self.mouse_ids(response), {'ALICE', 'BOB', 'UNKNOWN', 'OLD'})
        filtered = self.client.get(self.url, {'added_by': 'Alice', 'notes': 'Follow-Up'})
        fields = filtered.context['filter_form']['fields']
        self.assertEqual(next(field['value'] for field in fields if field['name'] == 'added_by'), 'Alice')
        self.assertEqual(next(field['value'] for field in fields if field['name'] == 'notes'), 'Follow-Up')
        self.assertContains(filtered, 'placeholder="Search username"')
        self.assertContains(filtered, 'placeholder="Search animal notes"')
        self.assertEqual(sum(field['type'] != 'checkbox' for field in fields), 8)

    def test_pagination_and_sort_links_keep_both_filters(self):
        Mouse.objects.bulk_create([Mouse(mouse_id=f'FILTER-{i}', protocol=self.protocol, added_by=self.alice, notes='Follow-up', protocol_start_date=self.current) for i in range(26)])
        response = self.client.get(self.url, {'added_by': 'Alice', 'notes': 'Follow', 'per_page': '25'})
        self.assertEqual(response.context['record_count']['count'], 28)
        urls = [response.context['pagination']['next_url']]
        urls += [column['sort_url'] for column in response.context['table']['columns'] if column.get('sort_url')]
        for url in urls:
            params = parse_qs(urlsplit(url).query)
            self.assertEqual(params['added_by'], ['Alice'])
            self.assertEqual(params['notes'], ['Follow'])
        clear = self.client.get(response.context['filter_form']['clear_url'])
        self.assertEqual(len(self.mouse_ids(clear)), 25)
        cleared_values = {field['name']: field['value'] for field in clear.context['filter_form']['fields']}
        self.assertEqual(cleared_values['added_by'], '')
        self.assertEqual(cleared_values['notes'], '')
