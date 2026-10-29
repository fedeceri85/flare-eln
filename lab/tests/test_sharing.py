from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import Client, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from ..models import Experiment, Mouse, Project, Protocol, Recording, RecordingType, SharedLink, ShareCodeCounter
from ..sharing import share_animals, share_recording, share_words
from ..ui import selected_recording_url
from ..views.mice import rename_mouse_identity


@override_settings(ALLOWED_HOSTS=['testserver'])
class SharingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='share-user', password='test-password')
        cls.other_user = get_user_model().objects.create_user(username='share-reader')
        cls.protocol = Protocol.objects.create(protocol_number=16, licence_reference='SHARE', name='Sharing')
        cls.a = Mouse.objects.create(mouse_id='Animal / A', protocol=cls.protocol, notes='First note')
        cls.b = Mouse.objects.create(mouse_id='Animal B', protocol=cls.protocol)
        cls.project = Project.objects.create(name='Sharing project')
        cls.kind = RecordingType.objects.get(slug='imaging')
        cls.experiment = Experiment.objects.create(project=cls.project, name='Sharing experiment', recording_type=cls.kind)
        cls.recording = Recording.objects.create(experiment=cls.experiment, recording_type=cls.kind,
                                                recording_date=date(2026, 10, 29), owner=cls.user)
        cls.recording.mice.add(cls.a)

    def setUp(self):
        self.client.force_login(self.user)

    def mouse_share(self, ids=None, tab='shared'):
        return share_animals(ids or [self.a.pk], tab, self.user)

    def link_url(self, link):
        return reverse('lab:shared_view', args=[link.code])

    def test_word_list_and_two_word_codes(self):
        self.assertGreater(len(share_words()), 7000)
        link = self.mouse_share()
        self.assertRegex(link.code, r'^m-[a-z]+-[a-z]+$')
        self.assertRegex(share_recording(self.recording.pk, self.user).code, r'^r-[a-z]+-[a-z]+$')

    def test_creation_payload_uses_short_link_and_reuses_animal_selection(self):
        response = self.client.post(reverse('lab:create_animal_share'), {
            'selected_mouse': [self.a.pk, self.b.pk], 'mouse_editor_tab': 'procedures',
        })
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['url'], 'http://testserver/s/' + payload['code'] + '/')
        second = self.client.post(reverse('lab:create_animal_share'), {
            'selected_mouse': [self.b.pk, self.a.pk, self.a.pk], 'mouse_editor_tab': 'procedures',
        })
        self.assertEqual(second.json(), payload)
        self.assertEqual(SharedLink.objects.count(), 1)
        self.assertEqual(ShareCodeCounter.objects.get(kind='m').allocated, 1)

    def test_recording_code_reuses_target_and_opens_selected_recording(self):
        url = reverse('lab:create_recording_share', args=[self.recording.pk])
        payload = self.client.post(url).json()
        self.client.force_login(self.other_user)
        self.assertEqual(self.client.post(url).json(), payload)
        self.assertEqual(ShareCodeCounter.objects.get(kind='r').allocated, 1)
        link = SharedLink.objects.get(code=payload['code'])
        self.assertRedirects(self.client.get(self.link_url(link)), selected_recording_url(self.recording), fetch_redirect_response=False)

    def test_animal_code_preserves_tab_and_shows_latest_records(self):
        link = self.mouse_share([self.a.pk, self.b.pk], tab='procedures')
        response = self.client.get(self.link_url(link))
        query = parse_qs(urlsplit(response.url).query)
        self.assertEqual(set(query['selected_mouse']), {self.a.pk, self.b.pk})
        self.assertEqual(query['mouse_editor_tab'], ['procedures'])
        Mouse.objects.filter(pk=self.a.pk).update(notes='Updated after sharing')
        self.assertContains(self.client.get(response.url), 'Updated after sharing')

    def test_renames_follow_animal_and_reuse_code(self):
        link = self.mouse_share()
        renamed = rename_mouse_identity(self.a.pk, 'Renamed animal')
        self.assertEqual(self.mouse_share([renamed.pk]).pk, link.pk)
        response = self.client.get(self.link_url(link))
        self.assertEqual(parse_qs(urlsplit(response.url).query)['selected_mouse'], [renamed.pk])
        self.assertEqual(ShareCodeCounter.objects.get(kind='m').allocated, 1)

    def test_deleted_animal_id_reuse_never_reconnects_old_code(self):
        link = self.mouse_share()
        old_pk = self.a.pk
        self.a.delete()
        replacement = Mouse.objects.create(mouse_id=old_pk, protocol=self.protocol)
        self.assertEqual(self.client.get(self.link_url(link)).status_code, 410)
        self.assertNotEqual(self.mouse_share([replacement.pk]).code, link.code)
        link.refresh_from_db()
        self.assertIsNone(link.animals.get().mouse_id)

    def test_partial_deletion_opens_survivors_with_notice(self):
        link = self.mouse_share([self.a.pk, self.b.pk], tab='individual')
        self.a.delete()
        response = self.client.get(self.link_url(link), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '1 animal in this selection is no longer available.')
        self.assertEqual([mouse.pk for mouse in response.context['selected_mice']], [self.b.pk])
        self.assertEqual(response.context['active_tab'], 'shared')

    def test_all_animals_deleted_shows_unavailable(self):
        link = self.mouse_share([self.a.pk, self.b.pk])
        self.a.delete()
        self.b.delete()
        self.assertContains(self.client.get(self.link_url(link)), 'These animals are no longer available.', status_code=410)

    def test_deleted_recording_keeps_code_reserved(self):
        link = share_recording(self.recording.pk, self.user)
        old_id = self.recording.pk
        self.recording.delete()
        self.assertContains(self.client.get(self.link_url(link)), 'This recording is no longer available.', status_code=410)
        replacement = Recording.objects.create(pk=old_id, experiment=self.experiment, recording_type=self.kind,
                                               recording_date=date(2026, 10, 29), owner=self.user)
        self.assertNotEqual(share_recording(replacement.pk, self.user).code, link.code)
        self.assertEqual(self.client.get(self.link_url(link)).status_code, 410)
        self.assertEqual(ShareCodeCounter.objects.get(kind='r').allocated, 2)

    def test_recording_destination_follows_current_experiment(self):
        link = share_recording(self.recording.pk, self.user)
        other = Experiment.objects.create(project=self.project, name='Moved experiment', recording_type=self.kind)
        self.recording.experiment = other
        self.recording.save()
        self.assertRedirects(self.client.get(self.link_url(link)), selected_recording_url(self.recording), fetch_redirect_response=False)

    def test_lowercase_uppercase_spaces_and_omitted_hyphens_resolve(self):
        link = self.mouse_share()
        for code in (link.code, link.code.upper(), link.code.replace('-', ' '), link.code.replace('-', '')):
            response = self.client.get(reverse('lab:open_share_code'), {'code': code}, HTTP_ACCEPT='application/json')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['url'], self.link_url(link))

    def test_invalid_code_stays_in_entry_interface(self):
        url = reverse('lab:open_share_code')
        response = self.client.get(url, {'code': 'm-not-a-real-code'}, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 404)
        self.assertIn('Code not found', response.json()['error'])
        self.assertContains(self.client.get(url, {'code': '<script>'}), 'Code not found', status_code=404)
        self.assertEqual(self.client.get(reverse('lab:shared_view', args=['r-unknown-code'])).status_code, 404)

    def test_full_page_entry_fallback(self):
        link = self.mouse_share()
        url = reverse('lab:open_share_code')
        self.assertContains(self.client.get(url), 'Open shared record')
        self.assertRedirects(self.client.get(url, {'code': link.code}), self.link_url(link), fetch_redirect_response=False)

    def test_login_required_and_login_preserves_short_destination(self):
        link = self.mouse_share()
        self.client.logout()
        url = self.link_url(link)
        response = self.client.get(url)
        self.assertEqual(urlsplit(response.url).path, reverse('lab:login'))
        self.assertEqual(parse_qs(urlsplit(response.url).query)['next'], [url])
        response = self.client.post(response.url, {'username': 'share-user', 'password': 'test-password', 'next': url})
        self.assertRedirects(response, url, fetch_redirect_response=False)

    def test_creation_requires_post_login_and_csrf(self):
        for url in (reverse('lab:create_animal_share'), reverse('lab:create_recording_share', args=[self.recording.pk])):
            self.assertEqual(self.client.get(url).status_code, 405)
            strict = Client(enforce_csrf_checks=True)
            strict.force_login(self.user)
            self.assertEqual(strict.post(url).status_code, 403)
            self.client.logout()
            self.assertEqual(self.client.post(url).status_code, 302)
            self.client.force_login(self.user)
        self.assertFalse(SharedLink.objects.exists())

    def test_invalid_targets_do_not_allocate_codes(self):
        for data in ({}, {'selected_mouse': ['Missing']}, {'selected_mouse': [self.a.pk, 'Missing']}):
            self.assertEqual(self.client.post(reverse('lab:create_animal_share'), data).status_code, 400)
        self.assertEqual(self.client.post(reverse('lab:create_recording_share', args=[999999])).status_code, 400)
        self.assertFalse(SharedLink.objects.exists())

    @override_settings(SHARE_CODE_TWO_WORD_LIMIT=1)
    def test_counter_switches_per_prefix_without_counting_share_table(self):
        first = self.mouse_share()
        self.assertEqual(len(first.code.split('-')), 3)
        with CaptureQueriesContext(connection) as queries:
            reused = self.mouse_share()
            second = self.mouse_share([self.b.pk])
        self.assertEqual(first.pk, reused.pk)
        self.assertEqual(len(second.code.split('-')), 4)
        self.assertTrue(ShareCodeCounter.objects.get(kind='m').use_three_words)
        self.assertFalse(any('COUNT(' in query['sql'].upper() for query in queries))
        self.assertEqual(len(share_recording(self.recording.pk, self.user).code.split('-')), 3)
        self.assertEqual(self.client.get(self.link_url(first)).status_code, 302)
        self.assertEqual(self.client.get(self.link_url(second)).status_code, 302)

    def test_collision_retries_and_falls_back_to_three_words(self):
        with patch('lab.sharing.generate_share_code', return_value='m-maple-otter'):
            first = self.mouse_share()
        with patch('lab.sharing.generate_share_code', side_effect=['m-maple-otter'] * 10 + ['m-maple-otter-copper']) as generator:
            second = self.mouse_share([self.b.pk])
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(generator.call_count, 11)
        self.assertEqual(generator.call_args.args, ('m', 3))
        self.assertEqual(ShareCodeCounter.objects.get(kind='m').allocated, 2)

    def test_collision_does_not_recycle_a_deleted_target_code(self):
        with patch('lab.sharing.generate_share_code', return_value='r-maple-otter'):
            share_recording(self.recording.pk, self.user)
        self.recording.delete()
        replacement = Recording.objects.create(experiment=self.experiment, recording_type=self.kind,
                                               recording_date=date(2026, 10, 29), owner=self.user)
        with patch('lab.sharing.generate_share_code', side_effect=['r-maple-otter', 'r-copper-meadow']):
            link = share_recording(replacement.pk, self.user)
        self.assertEqual(link.code, 'r-copper-meadow')
        self.assertTrue(SharedLink.objects.filter(code='r-maple-otter', recording=None).exists())

    def test_ui_has_enter_code_and_share_dialog_without_explanatory_note(self):
        response = self.client.get(reverse('lab:animal_summary', args=[self.a.pk]))
        self.assertContains(response, 'data-open-code>Enter code</a>')
        self.assertContains(response, 'data-share-code')
        self.assertContains(response, 'Copy code')
        self.assertContains(response, 'Copy link')
        self.assertNotContains(response, 'Login required. Opens the latest records.')


# SQLite verifies destination behavior above. These tests exercise the row locks
# used to serialize allocation on the deployed PostgreSQL database.
@skipUnlessDBFeature('has_select_for_update')
class ConcurrentSharingTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='concurrent-share')
        kind = RecordingType.objects.create(name='Concurrency', slug='sharing-concurrency')
        project = Project.objects.create(name='Concurrent sharing')
        experiment = Experiment.objects.create(project=project, name='Concurrent sharing', recording_type=kind)
        self.records = [Recording.objects.create(experiment=experiment, recording_type=kind,
                        recording_date=date(2026, 10, 29), owner=self.user) for _ in range(2)]

    def concurrent_codes(self, ids):
        gate = Barrier(len(ids))
        def create(recording_id):
            close_old_connections()
            try:
                gate.wait(timeout=10)
                return share_recording(recording_id, self.user).code
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(ids)) as executor:
            return list(executor.map(create, ids))

    def test_simultaneous_shares_reuse_one_code_for_the_same_recording(self):
        codes = self.concurrent_codes([self.records[0].pk, self.records[0].pk])
        self.assertEqual(codes[0], codes[1])
        self.assertEqual(SharedLink.objects.count(), 1)
        self.assertEqual(ShareCodeCounter.objects.get(kind='r').allocated, 1)

    def test_simultaneous_distinct_shares_increment_the_counter_without_lost_updates(self):
        codes = self.concurrent_codes([record.pk for record in self.records])
        self.assertNotEqual(codes[0], codes[1])
        self.assertEqual(SharedLink.objects.count(), 2)
        self.assertEqual(ShareCodeCounter.objects.get(kind='r').allocated, 2)
