"""Animal creation no longer scans for retrospective parent matches."""
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from ..models import Mouse, MouseLine, Protocol, Species


@override_settings(ALLOWED_HOSTS=['testserver'])
class ParentMatchDialogTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username='no-parent-popup'))
        self.protocol = Protocol.objects.create(protocol_number=882, licence_reference='DIALOG', name='Dialog')
        line = MouseLine.objects.create(species=Species.objects.get(name='Mouse'), name='Dialog line')
        self.crossing = line.crossings.get()
        self.child = Mouse.objects.create(mouse_id='Existing child', protocol=self.protocol, mother_external_id='New-1')
        self.destination = reverse('lab:mice_page') + '?status=Alive'

    def create(self, **extra):
        return self.client.post(reverse('lab:add_litter_all'), {
            'mouse_id_prefix': 'New-', 'mouse_numbers': '1-2', 'protocol': self.protocol.pk,
            'crossing_definition': self.crossing.pk, 'status': 'Alive', 'return_url': self.destination, **extra,
        })

    def test_matching_parent_creation_returns_directly_without_linking_or_popup(self):
        with patch('lab.family_maintenance.scan_missing_family_links', side_effect=AssertionError('Creation scanned retrospective links')):
            response = self.create()
        self.assertEqual(urlsplit(response.url).path, reverse('lab:view_selected_mice_all'))
        query = parse_qs(urlsplit(response.url).query)
        self.assertEqual(query['selected_mouse'], ['New-1', 'New-2'])
        self.assertEqual(query['return_url'], [self.destination])
        self.assertEqual(Mouse.objects.filter(mouse_id__startswith='New-').count(), 2)
        self.child.refresh_from_db()
        self.assertIsNone(self.child.mother_id)
        self.assertEqual(self.child.mother_external_id, 'New-1')
        summary = self.client.get(reverse('lab:animal_summary', args=['New-1']), {'mouse_editor_tab': 'family'})
        self.assertNotContains(summary, 'Matching external parent IDs')
        self.assertNotContains(summary, 'Link selected offspring')

    def test_recording_return_still_includes_created_animals(self):
        response = self.create(return_url='/mice/?mice=Existing%20child', return_with_mouse='1')
        self.assertEqual(urlsplit(response.url).path, reverse('lab:view_selected_mice_all'))
        destination = parse_qs(urlsplit(response.url).query)['return_url'][0]
        self.assertEqual(urlsplit(destination).path, '/mice/')
        values = parse_qs(urlsplit(destination).query)['mice'][0]
        for animal_id in ('Existing child', 'New-1', 'New-2'):
            self.assertIn(animal_id, values)

    def test_old_creation_review_bookmark_redirects_without_saving(self):
        url = reverse('lab:creation_parentage_review')
        self.assertRedirects(self.client.get(url, {'scope': 'retired'}), reverse('lab:maintenance'), fetch_redirect_response=False)
        self.assertEqual(self.client.post(url, {'scope': 'retired'}).status_code, 405)
