"""Global retrospective family resolution is explicit, paginated and stale-safe."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from ..family_maintenance import resolve_missing_family_links, scan_missing_family_links
from ..models import Mouse, Protocol
from ..parentage import graph_state, validate_parentage
from ..views.maintenance import SESSION_KEY


@override_settings(ALLOWED_HOSTS=['testserver'])
class FamilyMaintenanceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='maintenance-technician')
        self.client.force_login(self.user)
        self.protocol = Protocol.objects.create(protocol_number=870, licence_reference='MAINT', name='Maintenance')
        self.parent = Mouse.objects.create(mouse_id='Parent / 001', protocol=self.protocol, sex='F')
        self.child = Mouse.objects.create(mouse_id='Child', protocol=self.protocol, mother_external_id='  parent / 001  ')
        self.url = reverse('lab:maintenance')

    def scan(self):
        response = self.client.post(self.url, {'action': 'scan'})
        self.assertRedirects(response, self.url)
        return self.client.session[SESSION_KEY]

    def payload(self, scan, keys=None, **extra):
        return {'scan_id': scan['id'], 'current_page': '1', 'action': 'resolve',
                'family_link': keys if keys is not None else scan['selected'], **extra}

    def test_non_staff_access_settings_tab_and_scan_is_read_only(self):
        with patch('lab.parentage.graph_state', side_effect=AssertionError('Opening Settings scanned the database')):
            response = self.client.get(self.url)
        self.assertContains(response, 'Find matching parents')
        self.assertContains(response, 'it does not change any animals')
        self.assertContains(self.client.get(reverse('lab:settings_page')), self.url)
        scan = self.scan()
        self.assertEqual(scan['counts']['eligible'], 1)
        self.child.refresh_from_db()
        self.assertIsNone(self.child.mother_id)
        response = self.client.post(self.url, self.payload(scan))
        self.assertRedirects(response, self.url)
        self.child.refresh_from_db()
        self.assertEqual(self.child.mother_id, self.parent.pk)
        self.assertEqual(self.child.mother_external_id, '  parent / 001  ')
        self.assertNotIn(SESSION_KEY, self.client.session)
        # An old submitted form cannot repeat or change the completed operation.
        self.assertContains(self.client.post(self.url, self.payload(scan)), 'no longer current')

    def test_authentication_and_csrf_apply_to_scan_and_resolution(self):
        anonymous = Client()
        for method in ('get', 'post'):
            self.assertEqual(getattr(anonymous, method)(self.url).status_code, 302)
        csrf = Client(enforce_csrf_checks=True); csrf.force_login(self.user)
        self.assertEqual(csrf.get(self.url).status_code, 200)
        for action in ('scan', 'resolve'):
            self.assertEqual(csrf.post(self.url, {'action': action}).status_code, 403)

    def test_pagination_retains_deselection_and_resolves_selected_links_on_all_pages(self):
        Mouse.objects.bulk_create([Mouse(mouse_id=f'Extra-{i:03}', protocol=self.protocol, mother_external_id=self.parent.pk) for i in range(54)])
        scan = self.scan()
        page = self.client.get(self.url).context['page_obj']
        self.assertEqual(len(page), 50)
        keys = [entry['key'] for entry in page]
        response = self.client.post(self.url, self.payload(scan, keys[1:], action='', next_page='2'))
        self.assertRedirects(response, self.url + '?page=2')
        page2 = self.client.get(self.url + '?page=2').context['page_obj']
        self.assertEqual(len(page2), 5)
        scan = self.client.session[SESSION_KEY]
        response = self.client.post(self.url, self.payload(scan, [entry['key'] for entry in page2], current_page='2'))
        self.assertRedirects(response, self.url)
        self.assertEqual(Mouse.objects.filter(mother=self.parent).count(), 54)
        self.child.refresh_from_db()
        self.assertIsNone(self.child.mother_id)  # First displayed row was deselected.

    def test_stale_candidate_and_forged_selection_never_link_silently(self):
        scan = self.scan()
        response = self.client.post(self.url, self.payload(scan, ['forged']))
        self.assertContains(response, 'eligible links from this page')
        self.child.mother_external_id = 'Corrected ID'; self.child.save()
        response = self.client.post(self.url, self.payload(scan))
        self.assertContains(response, 'Animal information changed since this scan')
        self.assertTrue(self.client.session[SESSION_KEY]['stale'])
        self.child.refresh_from_db(); self.assertIsNone(self.child.mother_id)

    def test_ambiguous_invalid_unmatched_and_already_linked_slots(self):
        Mouse.objects.create(mouse_id='Ambiguous', protocol=self.protocol)
        Mouse.objects.create(mouse_id='AMBIGUOUS', protocol=self.protocol)
        Mouse.objects.create(mouse_id='Ambiguous child', protocol=self.protocol, father_external_id='ambiguous')
        Mouse.objects.create(mouse_id='Unknown child', protocol=self.protocol, father_external_id='External only')
        Mouse.objects.create(mouse_id='Linked child', protocol=self.protocol, mother=self.parent, mother_external_id=self.parent.pk)
        self.parent.mother_external_id = self.parent.pk; self.parent.save()
        scan = self.scan()
        self.assertEqual(scan['counts'], {'proposed': 3, 'eligible': 1, 'issues': 2, 'unmatched': 1})
        self.assertEqual({entry['mouse_id'] for entry in scan['entries']}, {'Child', 'Ambiguous child', self.parent.pk})

    def test_final_batch_cycle_is_blocked_and_individual_deselection_can_proceed(self):
        a = Mouse.objects.create(mouse_id='A', protocol=self.protocol, mother_external_id='B')
        b = Mouse.objects.create(mouse_id='B', protocol=self.protocol, mother_external_id='A')
        scan = scan_missing_family_links()
        with self.assertRaisesMessage(ValidationError, 'cycle'):
            resolve_missing_family_links(scan['fingerprint'], [entry['key'] for entry in scan['entries'] if entry['eligible']])
        a.refresh_from_db(); b.refresh_from_db()
        self.assertIsNone(a.mother_id); self.assertIsNone(b.mother_id)
        chosen = [entry['key'] for entry in scan['entries'] if entry['mouse_id'] == a.pk]
        resolve_missing_family_links(scan['fingerprint'], chosen)
        a.refresh_from_db(); self.assertEqual(a.mother_id, b.pk)

    def test_scans_and_resolution_read_graph_once_and_validate_once(self):
        Mouse.objects.bulk_create([Mouse(mouse_id=f'Batch-{i}', protocol=self.protocol, mother_external_id=self.parent.pk) for i in range(100)])
        with CaptureQueriesContext(connection) as queries:
            scan = scan_missing_family_links()
        self.assertEqual(len(queries), 1)
        with patch('lab.parentage.graph_state', wraps=graph_state) as reads, patch('lab.family_maintenance.validate_parentage', wraps=validate_parentage) as validations:
            result = resolve_missing_family_links(scan['fingerprint'], [entry['key'] for entry in scan['entries'] if entry['eligible']])
        self.assertEqual(reads.call_count, 1)
        self.assertEqual(validations.call_count, 1)
        self.assertEqual(result['links'], 101)

    def test_new_matches_require_a_fresh_scan_and_cross_protocol_links_are_allowed(self):
        other = Protocol.objects.create(protocol_number=871, licence_reference='OTHER-MAINT', name='Other protocol')
        self.child.protocol = other; self.child.save()
        scan = self.scan()
        newcomer = Mouse.objects.create(mouse_id='New match', protocol=self.protocol, mother_external_id=self.parent.pk)
        response = self.client.post(self.url, self.payload(scan))
        self.assertContains(response, 'Animal information changed')
        self.child.refresh_from_db(); newcomer.refresh_from_db()
        self.assertIsNone(self.child.mother_id); self.assertIsNone(newcomer.mother_id)
        refreshed = self.scan()
        self.client.post(self.url, self.payload(refreshed))
        self.child.refresh_from_db(); self.assertEqual(self.child.mother_id, self.parent.pk)

    def test_warning_matches_remain_eligible_and_scan_expires(self):
        from datetime import date
        self.parent.sex = 'M'; self.parent.date_of_birth = date(2026, 12, 1); self.parent.save()
        self.child.date_of_birth = date(2026, 1, 1); self.child.save()
        scan = self.scan()
        self.assertTrue(scan['entries'][0]['eligible'])
        self.assertEqual(len(scan['entries'][0]['warnings']), 2)
        session = self.client.session
        expired = dict(session[SESSION_KEY]); expired['created'] = 0
        session[SESSION_KEY] = expired; session.save()
        response = self.client.post(self.url, self.payload(scan))
        self.assertContains(response, 'no longer current')
        self.child.refresh_from_db(); self.assertIsNone(self.child.mother_id)
        scan = self.scan()
        response = self.client.post(self.url, self.payload(scan))
        self.assertRedirects(response, self.url)
        self.child.refresh_from_db(); self.assertEqual(self.child.mother_id, self.parent.pk)

    def test_a_scan_cannot_be_used_by_another_user(self):
        scan = self.scan()
        other = get_user_model().objects.create_user(username='other-technician')
        client = Client(); client.force_login(other)
        response = client.post(self.url, self.payload(scan))
        self.assertContains(response, 'no longer current')
        self.child.refresh_from_db(); self.assertIsNone(self.child.mother_id)
