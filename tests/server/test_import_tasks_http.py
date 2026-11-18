"""HTTP acceptance stays fast while a slow URL is fetched in the background."""
import json
import threading
import time
import unittest
from unittest.mock import Mock, patch

from easyread.library.store import Workspace, write_json_atomic
from tests.server import test_access


class ImportTasksHTTPTest(unittest.TestCase):
    request = test_access.AccessHTTPTest.request
    login = test_access.AccessHTTPTest.login

    def setUp(self):
        test_access.AccessHTTPTest.setUp(self)
        self.cookie = self.login()
        self.release = threading.Event()
        folder = self.app.lib.root / 'import-paper'; folder.mkdir()
        self.ws = Workspace(folder)
        write_json_atomic(self.ws.paper_path, {'meta':{'pages':[{'n':1}]}, 'blocks':[], 'translation':{}})
        self.app.jobs.enqueue = Mock()
        self.body = {'ref':'https://example.test/slow', 'kind':'web', 'translate':False, 'request_id':'http-request-0001'}

    def tearDown(self):
        self.release.set()
        imports = self.app.import_tasks
        if imports:
            imports.close()
            if imports.worker and imports.worker.is_alive(): imports.worker.join(3)
        test_access.AccessHTTPTest.tearDown(self)

    def test_fast_acceptance_monitor_and_scoped_status(self):
        started = threading.Event()
        def fetch(*args, **kwargs):
            started.set(); self.release.wait(3); return self.ws, True
        with patch.object(self.app.lib,'import_reference',side_effect=fetch) as source:
            before = time.monotonic()
            status, _, body = self.request('POST','/api/import-tasks',self.body,cookie=self.cookie,token=self.app.token)
            self.assertEqual(status,202); self.assertLess(time.monotonic()-before,1)
            task = json.loads(body)['task']; identity = task['id']
            self.assertTrue(started.wait(1)); self.assertEqual(self.app.location.active,1)
            status,_,body = self.request('GET','/api/import-tasks',cookie=self.cookie)
            listing = json.loads(body)
            self.assertEqual(status,200); self.assertRegex(listing['library_id'],r'^[a-f0-9]{24}$')
            self.assertEqual(listing['tasks'][0]['id'],identity)
            status,_,body = self.request('GET','/api/research/v1/monitor',cookie=self.cookie)
            self.assertEqual(status,200)
            self.assertIn(identity,[entry['id'] for entry in json.loads(body)['tasks']])
            wrong = {**self.body, 'library_scope':'wrong-library'}
            self.assertEqual(self.request('POST','/api/import-tasks',wrong,cookie=self.cookie,token=self.app.token)[0],409)
            self.release.set()
            deadline = time.monotonic()+3
            while time.monotonic()<deadline:
                _,_,body = self.request('GET','/api/import-tasks/'+identity,cookie=self.cookie)
                if json.loads(body)['task']['state']=='completed': break
                time.sleep(.01)
            self.assertEqual(json.loads(body)['task']['result']['id'],self.ws.id)
            status,_,_ = self.request('POST','/api/import-tasks',self.body,cookie=self.cookie,token=self.app.token)
            self.assertEqual(status,202); source.assert_called_once()

    def test_authentication_csrf_and_request_validation_remain_required(self):
        self.assertEqual(self.request('POST','/api/import-tasks',self.body)[0],401)
        self.assertEqual(self.request('GET','/api/import-tasks')[0],401)
        self.assertEqual(self.request('POST','/api/import-tasks',self.body,cookie=self.cookie)[0],403)
        self.assertEqual(self.request('POST','/api/import-tasks',[],cookie=self.cookie,token=self.app.token)[0],400)
        self.assertEqual(self.request('GET','/api/import-tasks/not-an-id',cookie=self.cookie)[0],404)

    def test_legacy_url_endpoint_keeps_its_synchronous_result_contract(self):
        with patch.object(self.app.lib,'import_reference',return_value=(self.ws,True)) as source:
            status,_,body = self.request('POST','/api/import-url',self.body,cookie=self.cookie,token=self.app.token)
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body),{'id':self.ws.id,'new':True,'queued':True})
        source.assert_called_once_with(self.body['ref'],'web')
        self.assertIsNone(self.app.import_tasks)


if __name__ == '__main__':
    unittest.main()
