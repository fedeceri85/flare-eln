"""Real PostgreSQL transactions prove graph writes serialize across connections."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from ..models import Mouse, Protocol
from ..parentage import apply_parentage, parentage_transaction
from ..views.mice import rename_mouse_identity


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL advisory transaction locks')
class ParentageConcurrencyTests(TransactionTestCase):
    def setUp(self):
        protocol = Protocol.objects.create(protocol_number=879, licence_reference='CONCURRENT', name='Concurrency')
        for pk in ('A', 'B'):
            Mouse.objects.create(mouse_id=pk, protocol=protocol)

    def worker(self, callback):
        close_old_connections()
        try:
            return callback()
        finally:
            close_old_connections()

    def test_concurrent_opposing_parent_links_cannot_create_cycle(self):
        barrier = Barrier(2)
        def link(child, parent):
            barrier.wait(timeout=5)
            try:
                apply_parentage({child: {'mother_id': parent, 'mother_external_id': parent}})
                return 'saved'
            except ValidationError:
                return 'invalid'
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.worker, lambda: link('A', 'B')), pool.submit(self.worker, lambda: link('B', 'A'))]
            self.assertCountEqual([future.result(timeout=10) for future in futures], ['saved', 'invalid'])

    def test_delete_waits_for_parent_edit_and_retains_recorded_identifier(self):
        acquired, release = Event(), Event()
        @parentage_transaction
        def edit():
            acquired.set()
            if not release.wait(5): raise RuntimeError('Timed out')
            apply_parentage({'B': {'mother_id': 'A', 'mother_external_id': 'Original A'}})
        @parentage_transaction
        def delete():
            Mouse.objects.get(pk='A').delete()
        with ThreadPoolExecutor(max_workers=2) as pool:
            editor = pool.submit(self.worker, edit)
            self.assertTrue(acquired.wait(5))
            deleter = pool.submit(self.worker, delete)
            self.assertFalse(deleter.done())
            release.set()
            editor.result(timeout=10); deleter.result(timeout=10)
        child = Mouse.objects.get(pk='B')
        self.assertIsNone(child.mother_id)
        self.assertEqual(child.mother_external_id, 'Original A')

    def test_rename_waits_for_parent_edit_and_moves_link(self):
        acquired, release = Event(), Event()
        @parentage_transaction
        def edit():
            acquired.set()
            if not release.wait(5): raise RuntimeError('Timed out')
            apply_parentage({'B': {'father_id': 'A', 'father_external_id': 'A'}})
        with ThreadPoolExecutor(max_workers=2) as pool:
            editor = pool.submit(self.worker, edit)
            self.assertTrue(acquired.wait(5))
            renamer = pool.submit(self.worker, lambda: rename_mouse_identity('A', 'New A'))
            release.set()
            editor.result(timeout=10); renamer.result(timeout=10)
        child = Mouse.objects.get(pk='B')
        self.assertEqual(child.father_id, 'New A')
        self.assertEqual(child.father_external_id, 'A')

    def test_concurrent_global_maintenance_scans_cannot_apply_stale_opposing_links(self):
        from ..family_maintenance import scan_missing_family_links, resolve_missing_family_links
        Mouse.objects.filter(pk='A').update(mother_external_id='B')
        Mouse.objects.filter(pk='B').update(mother_external_id='A')
        scan = scan_missing_family_links()
        barrier = Barrier(2)
        def resolve(pk):
            selected = [entry['key'] for entry in scan['entries'] if entry['mouse_id'] == pk]
            barrier.wait(timeout=5)
            try:
                resolve_missing_family_links(scan['fingerprint'], selected)
                return 'saved'
            except ValidationError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.worker, lambda: resolve('A')), pool.submit(self.worker, lambda: resolve('B'))]
            self.assertCountEqual([future.result(timeout=10) for future in futures], ['saved', 'stale'])
