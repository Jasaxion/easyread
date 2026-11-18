"""Browser URL imports: durable acceptance, cancellation, retries and unchanged handoff."""
from concurrent.futures import CancelledError
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from easyread.library.library import Library
from easyread.library.store import Workspace, write_json_atomic
from easyread.research import tasks
from easyread.server.import_tasks import ImportTasks, ImportTaskError
from easyread.server.library_api import LibraryLocation
from easyread.server import import_tasks


class ImportTasksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        lib = Library(Path(self.tmp.name))
        self.app = SimpleNamespace(lib=lib, jobs=Mock(), babeldoc=Mock(), presence=None)
        self.app.location = LibraryLocation(self.app)
        self.imports = ImportTasks(self.app)
        self.release = threading.Event()
        self.addCleanup(self.stop)
        folder = lib.root / 'sample-paper'; folder.mkdir()
        self.ws = Workspace(folder)
        write_json_atomic(self.ws.paper_path, {'meta': {'pages': [{'n': 1}], 'page_count': 1}, 'blocks': [], 'translation': {}})
        self.body = {'ref': 'https://example.test/article', 'request_id': 'request-0001', 'translate': False}

    def stop(self):
        self.release.set(); self.imports.close()
        if self.imports.worker and self.imports.worker.is_alive():
            self.imports.worker.join(3)
        self.assertEqual(self.app.location.active, 0)

    def wait(self, identity):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            task = self.imports.get(identity)
            if task['state'] not in ('queued', 'running', 'cancelling'):
                return task
            time.sleep(.005)
        self.fail('Import did not terminate')

    def test_submit_returns_before_fetch_and_same_request_id_never_repeats(self):
        started = threading.Event()
        def fetch(*args, **kwargs):
            started.set(); self.assertTrue(self.release.wait(3)); return self.ws, True
        with patch.object(self.app.lib, 'import_reference', side_effect=fetch) as source:
            before = time.monotonic(); first = self.imports.submit(self.body)
            self.assertLess(time.monotonic() - before, .5)
            self.assertTrue(started.wait(1)); self.assertEqual(self.app.location.active, 1)
            again = self.imports.submit(self.body)
            self.assertEqual(first['id'], again['id'])
            with self.assertRaises(ImportTaskError) as caught:
                self.imports.submit({**self.body, 'ref': 'different'})
            self.assertEqual(caught.exception.status, 409)
            self.release.set(); done = self.wait(first['id'])
            self.assertEqual(done['state'], 'completed')
            self.assertEqual(done['paper_id'], self.ws.id)
            self.assertEqual(done['result']['id'], self.ws.id)
            self.assertEqual(self.imports.submit(self.body)['id'], first['id'])
            source.assert_called_once()
        self.app.jobs.enqueue.assert_called_once_with(self.ws, translate_after=False, scope='all', read=False, model='', translation_extra='')
        visible = next(t for t in tasks.list_tasks(scope=self.imports.scope) if t['id'] == first['id'])
        self.assertEqual(visible['request_id'], self.body['request_id'])
        self.assertEqual(visible['paper_id'], self.ws.id)
        self.assertNotIn('result', visible)

    def test_queue_full_and_queued_cancel_release_slot_without_fetch(self):
        self.imports.LIMIT = 2
        started = threading.Event()
        def fetch(*args, **kwargs):
            started.set(); self.release.wait(3); return self.ws, True
        with patch.object(self.app.lib, 'import_reference', side_effect=fetch) as source:
            first = self.imports.submit(self.body); self.assertTrue(started.wait(1))
            second = self.imports.submit({**self.body, 'request_id': 'request-0002'})
            with self.assertRaises(ImportTaskError) as caught:
                self.imports.submit({**self.body, 'request_id': 'request-0003'})
            self.assertEqual(caught.exception.status, 429)
            self.assertEqual(self.imports.cancel(second['id'])['state'], 'cancelled')
            self.assertEqual(self.app.location.active, 1)
            self.release.set(); self.wait(first['id'])
            source.assert_called_once()

    def test_cancel_after_document_commit_retains_document_without_translation(self):
        started = threading.Event()
        def fetch(*args, **kwargs):
            started.set(); self.release.wait(3); return self.ws, True
        with patch.object(self.app.lib, 'import_reference', side_effect=fetch):
            task = self.imports.submit({**self.body, 'translate': True})
            self.assertTrue(started.wait(1))
            self.assertEqual(self.imports.cancel(task['id'])['state'], 'cancelling')
            self.release.set(); done = self.wait(task['id'])
        self.assertEqual(done['state'], 'cancelled')
        self.assertEqual(done['result']['id'], self.ws.id)
        self.app.jobs.enqueue.assert_not_called()
        self.assertTrue(self.ws.paper_path.exists())

    def test_retry_finishes_requested_translation_after_cancelled_web_handoff(self):
        self.ws.update('paper', lambda p: p['meta'].update(source_type='web'))
        started = threading.Event()
        def fetch(*args, **kwargs):
            started.set(); self.release.wait(3); return self.ws, True
        with patch.object(self.app.lib, 'import_reference', side_effect=fetch):
            original = self.imports.submit({**self.body, 'translate':True})
            self.assertTrue(started.wait(1)); self.imports.cancel(original['id'])
            self.release.set(); self.wait(original['id'])
        self.app.jobs.enqueue.assert_not_called()
        with patch.object(self.app.lib, 'import_reference', return_value=(self.ws, False)):
            child = self.imports.retry(original['id']); done = self.wait(child['id'])
        self.assertEqual(done['state'], 'completed')
        self.assertTrue(done['result']['queued'])
        self.app.jobs.enqueue.assert_called_once()

    def test_retry_is_explicit_and_repeated_retry_returns_same_child(self):
        with patch.object(self.app.lib, 'import_reference', side_effect=ValueError('unavailable')):
            original = self.imports.submit(self.body); self.assertEqual(self.wait(original['id'])['state'], 'failed')
        with patch.object(self.app.lib, 'import_reference', return_value=(self.ws, True)) as source:
            child = self.imports.retry(original['id']); self.wait(child['id'])
            self.assertEqual(child['retry_of'], original['id'])
            self.assertEqual(self.imports.retry(original['id'])['id'], child['id'])
            self.assertEqual(self.imports.submit(self.body)['state'], 'failed')
            source.assert_called_once()
        self.assertTrue(self.imports.ack(child['id'])['acknowledged'])

    def test_restart_retains_interrupted_task_without_reexecuting(self):
        with patch('easyread.server.import_tasks.threading.Thread.start'):
            task = self.imports.submit(self.body)
        self.imports.close()
        new = ImportTasks(self.app)
        self.assertEqual(new.get(task['id'])['state'], 'interrupted')
        self.assertIsNone(new.worker)
        self.assertEqual(new.submit(self.body)['id'], task['id'])
        self.app.jobs.enqueue.assert_not_called()
        new.close(); self.imports.worker = None

    def test_options_are_captured_and_requested_pdf_export_is_started_once(self):
        body = {**self.body, 'translate': True, 'read': True, 'scope': 'first:2', 'model': 'chosen',
                'target': 'ja', 'translation_extra': 'Keep terms', 'pdf_export': True, 'pdf_output_dir': '/chosen'}
        self.app.babeldoc.start.return_value = {'state': 'running'}
        with patch.object(self.app.lib, 'import_reference', return_value=(self.ws, True)):
            task = self.imports.submit(body); done = self.wait(task['id'])
        self.app.jobs.enqueue.assert_called_once_with(self.ws, translate_after=True, scope='first:2', read=True, model='chosen', translation_extra='Keep terms')
        self.app.babeldoc.start.assert_called_once_with(self.ws, model='chosen', output_dir='/chosen', regenerate=False)
        self.assertEqual(done['state'], 'completed')

    def test_existing_document_is_not_requeued_and_export_failure_is_partial(self):
        self.app.babeldoc.start.side_effect = ValueError('export unavailable')
        with patch.object(self.app.lib, 'import_reference', return_value=(self.ws, False)):
            task = self.imports.submit({**self.body, 'pdf_export': True}); done = self.wait(task['id'])
        self.app.jobs.enqueue.assert_not_called()
        self.assertEqual(done['state'], 'completed')
        self.assertEqual(done['result_state'], 'partial')
        self.assertIn('export unavailable', done['error_reason'])
        self.assertFalse(done['result']['new'])

    def test_pdf_only_retry_preserves_options_without_reimport_or_reader_translation(self):
        self.app.babeldoc.start.side_effect = [ValueError('not ready'), {'state':'running'}]
        with patch.object(self.app.lib, 'import_reference', return_value=(self.ws, True)) as source:
            original = self.imports.submit({**self.body, 'translate':True, 'pdf_export':True, 'model':'chosen', 'pdf_output_dir':'/chosen'})
            self.assertEqual(self.wait(original['id'])['result_state'],'partial')
            child = self.imports.retry(original['id']); done = self.wait(child['id'])
            self.assertTrue(child['retry_pdf'])
            self.assertEqual(done['state'],'completed')
            self.assertEqual(self.imports.retry(original['id'])['id'],child['id'])
            source.assert_called_once()
        self.app.jobs.enqueue.assert_called_once()
        self.assertEqual(self.app.babeldoc.start.call_count,2)
        self.assertEqual(self.app.babeldoc.start.call_args.args[0].id, self.ws.id)
        self.assertEqual(self.app.babeldoc.start.call_args.kwargs, {'model':'chosen','output_dir':'/chosen','regenerate':False})

    def test_duplicate_acceptance_requires_a_durable_record_after_disk_failure(self):
        with patch.object(tasks, '_persist'), patch.object(import_tasks, 'write_json_atomic', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.imports.submit(self.body)
            with self.assertRaises(OSError): self.imports.submit(self.body)
        self.assertFalse(list(self.imports.root.glob('*.json')))
        accepted = self.imports.submit(self.body)
        self.assertEqual(accepted['state'],'failed')
        self.assertTrue((self.imports.root / (accepted['id']+'.json')).is_file())
        self.app.jobs.enqueue.assert_not_called()

    def test_monitor_keeps_active_imports_when_more_than_twenty_new_tasks_exist(self):
        first = tasks.register_external('a'*32, 'import', scope=self.imports.scope, state='running')
        for n in range(25):
            tasks.register_external(f'{n:032x}', 'mindmap', scope=self.imports.scope, state='completed')
        self.assertIn(first['id'], [task['id'] for task in tasks.list_tasks(scope=self.imports.scope)])
        tasks.update_external(first['id'], state='cancelled')

    def test_explicit_retry_can_reserve_translation_behind_existing_preparation(self):
        with patch.object(self.app.lib,'import_reference',side_effect=ValueError('initial failure')):
            original=self.imports.submit({**self.body,'translate':True}); self.wait(original['id'])
        self.ws.update('job',lambda job:job.update(type='prepare',state='running',translate=False))
        with patch.object(self.app.lib,'import_reference',return_value=(self.ws,False)):
            child=self.imports.retry(original['id']); done=self.wait(child['id'])
        self.assertTrue(done['result']['queued'])
        self.app.jobs.enqueue.assert_called_once()

    def test_concurrent_legacy_handoff_is_not_scheduled_again(self):
        self.ws.update("job", lambda job: job.update(state="queued", type="translate"))
        with patch.object(self.app.lib, "import_reference", return_value=(self.ws, True)):
            task = self.imports.submit(self.body); done = self.wait(task["id"])
        self.assertEqual(done["state"], "completed")
        self.assertFalse(done["result"]["queued"])
        self.app.jobs.enqueue.assert_not_called()

    def test_failed_durable_acceptance_never_starts_network_work(self):
        with patch.object(import_tasks, 'write_json_atomic', side_effect=OSError('disk full')), patch.object(self.app.lib, 'import_reference') as source:
            with self.assertRaises(OSError): self.imports.submit(self.body)
        source.assert_not_called(); self.assertEqual(self.app.location.active, 0)
        self.assertFalse(self.imports.pending)

    def test_close_releases_queue_and_stops_worker_even_when_task_writes_fail(self):
        started = threading.Event()
        def fetch(*args, **kwargs):
            started.set(); self.release.wait(3); return self.ws, True
        with patch.object(self.app.lib,'import_reference',side_effect=fetch) as source:
            self.imports.submit(self.body); self.assertTrue(started.wait(1))
            self.imports.submit({**self.body,'request_id':'request-0002'})
            with patch.object(tasks,'_persist'), patch.object(import_tasks,'write_json_atomic',side_effect=OSError('disk full')):
                self.imports.close()
                self.assertFalse(self.imports.pending)
                self.assertEqual(self.app.location.active,1)
                self.assertTrue(source.call_args.kwargs['cancel'].is_set())
                self.release.set(); self.imports.worker.join(3)
                self.assertFalse(self.imports.worker.is_alive())
        self.assertEqual(self.app.location.active,0)
        self.app.jobs.enqueue.assert_not_called()

    def test_handoff_does_not_hold_submit_lock_or_allow_late_cancellation(self):
        entered = threading.Event()
        def export(*args, **kwargs):
            entered.set(); self.release.wait(3); return {'state':'running'}
        self.app.babeldoc.start.side_effect = export
        with patch.object(self.app.lib, 'import_reference', return_value=(self.ws, True)):
            first = self.imports.submit({**self.body, 'pdf_export': True})
            self.assertTrue(entered.wait(1))
            self.assertEqual(self.imports.cancel(first['id'])['phase'], 'handoff')
            before = time.monotonic(); second = self.imports.submit({**self.body, 'request_id':'request-0002'})
            self.assertLess(time.monotonic()-before,.5)
            self.imports.cancel(second['id']); self.release.set(); self.wait(first['id'])

    def test_invalid_options_and_cross_library_access(self):
        for patch_body in ({'ref':''}, {'translate':'false'}, {'kind':'unknown'}, {'request_id':'bad'}):
            with self.assertRaises(ValueError): self.imports.submit({**self.body, **patch_body})
        with self.assertRaises(ImportTaskError) as caught: self.imports.get('../escape')
        self.assertEqual(caught.exception.status,404)

    def test_library_cancel_checkpoint_prevents_new_document_write(self):
        event = threading.Event()
        def fetch(_):
            event.set(); return b'%PDF-test', 'test.pdf', {}
        before = len(self.app.lib.all())
        with patch.object(self.app.lib, 'fetch', side_effect=fetch):
            with self.assertRaises(CancelledError):
                self.app.lib.import_reference('https://example.test/file.pdf', cancel=event)
        self.assertEqual(len(self.app.lib.all()),before)


if __name__ == '__main__':
    unittest.main()
