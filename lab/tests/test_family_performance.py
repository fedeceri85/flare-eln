"""Family reads must scale with relatives, not unrelated colony records."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from ..family import build_family_sections
from ..models import Mouse, Protocol
from ..parentage import ParentageContext, graph_state, reconcile_many, reconciliation_fingerprint, validate_parentage


@override_settings(ALLOWED_HOSTS=['testserver'])
class FamilyPerformanceTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username='family-performance'))
        self.protocol = Protocol.objects.create(protocol_number=708, licence_reference='PERF', name='Performance')
        self.parent = Mouse.objects.create(mouse_id='Parent / 001', protocol=self.protocol)
        Mouse.objects.bulk_create([Mouse(mouse_id=f'Child-{i:04}', protocol=self.protocol, mother=self.parent) for i in range(50)])

    def test_other_tabs_never_load_family_or_graph_and_family_reads_are_bounded(self):
        with patch('lab.parentage.graph_state', side_effect=AssertionError('Full graph read')):
            page = self.client.get(reverse('lab:animal_summary', args=[self.parent.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertFalse(page.context['family_loaded'])
        self.assertNotContains(page, 'Offspring recorded in this ELN')
        with patch('lab.parentage.graph_state', side_effect=AssertionError('Full graph read')):
            with CaptureQueriesContext(connection) as before:
                first = build_family_sections([self.parent])
            Mouse.objects.bulk_create([Mouse(mouse_id=f'Unrelated-{i}', protocol=self.protocol) for i in range(2000)])
            with CaptureQueriesContext(connection) as after:
                second = build_family_sections([self.parent])
        self.assertEqual(len(before), 4)
        self.assertEqual(len(after), 4)
        self.assertEqual(first, second)
        self.assertEqual(second[0]['offspring_count'], 50)
        self.assertEqual(len(second[0]['groups'][0]['preview']), 5)
        self.assertNotIn('members', second[0]['groups'][0])

    def test_family_fragment_and_direct_tab_do_not_run_reconciliation(self):
        for path, params in [
            (reverse('lab:family_fragment'), {'selected_mouse': self.parent.pk}),
            (reverse('lab:animal_summary', args=[self.parent.pk]), {'mouse_editor_tab': 'family'}),
        ]:
            with patch('lab.parentage.graph_state', side_effect=AssertionError('Full graph read')):
                response = self.client.get(path, params, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
            self.assertContains(response, 'Offspring recorded in this ELN · 50')
            self.assertNotContains(response, 'data-family-matching')
            self.assertNotContains(response, 'name="candidate_token"')

    def test_fragment_authentication_and_no_script_navigation(self):
        url = reverse('lab:family_fragment')
        self.assertEqual(Client().get(url, {'selected_mouse': self.parent.pk}).status_code, 302)
        response = self.client.get(url, {'selected_mouse': self.parent.pk, 'return_url': 'https://untrusted.example/'})
        self.assertContains(response, '<!DOCTYPE html>')
        self.assertNotContains(response, 'href="https://untrusted.example/')
        response = self.client.get(url, {'selected_mouse': 'Missing'})
        self.assertEqual(response.status_code, 404)

    def test_candidate_checks_and_reconciliation_use_one_graph_per_batch(self):
        other = Mouse.objects.create(mouse_id='Other parent', protocol=self.protocol)
        Mouse.objects.bulk_create([Mouse(mouse_id=f'Unresolved-{i:04}', protocol=self.protocol, mother_external_id=self.parent.pk, father_external_id=other.pk) for i in range(100)])
        context = ParentageContext()
        selections = {}
        with patch('lab.parentage.validate_parentage', side_effect=AssertionError('Per-candidate whole graph validation')):
            for pk in (self.parent.pk, other.pk):
                candidates = context.matching_candidates(pk)
                self.assertEqual(len(candidates), 100)
                selections[pk] = {'selected': [c['key'] for c in candidates], 'expected': reconciliation_fingerprint(pk, candidates, context.state)}
        with patch('lab.parentage.graph_state', wraps=graph_state) as reads, patch('lab.parentage.validate_parentage', wraps=validate_parentage) as validations:
            reconcile_many(selections)
        self.assertEqual(reads.call_count, 1)
        self.assertEqual(validations.call_count, 1)
        child = Mouse.objects.get(pk='Unresolved-0000')
        self.assertEqual((child.mother_id, child.father_id), (self.parent.pk, other.pk))

    def test_individually_eligible_reconciliations_cannot_create_a_batch_cycle(self):
        a = Mouse.objects.create(mouse_id='Cycle A', protocol=self.protocol, mother_external_id='Cycle B')
        b = Mouse.objects.create(mouse_id='Cycle B', protocol=self.protocol, mother_external_id='Cycle A')
        context = ParentageContext()
        selections = {}
        for pk in (a.pk, b.pk):
            candidates = context.matching_candidates(pk)
            self.assertTrue(all(candidate['eligible'] for candidate in candidates))
            selections[pk] = {'selected': [candidate['key'] for candidate in candidates],
                              'expected': reconciliation_fingerprint(pk, candidates, context.state)}
        with self.assertRaisesMessage(ValidationError, 'cycle'):
            reconcile_many(selections)
        a.refresh_from_db(); b.refresh_from_db()
        self.assertIsNone(a.mother_id)
        self.assertIsNone(b.mother_id)
