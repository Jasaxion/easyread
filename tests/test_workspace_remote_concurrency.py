"""Remote creation/transition reservations must never starve healthy cancellation."""
import threading
import unittest
from unittest.mock import patch

from easyread.library.store import write_json_atomic
from easyread.workspaces import WorkspaceError, latex, remote_transport, remote_worker
from easyread.workspaces.remote import Remotes
from easyread.workspaces.remote_papers import ReferenceStore
from tests import test_workspace_remote_http as fixture


class WorkspaceRemoteConcurrencyTest(unittest.TestCase):
    setUp = fixture.WorkspaceRemoteHTTPTest.setUp
    tearDown = fixture.WorkspaceRemoteHTTPTest.tearDown
    setUp_local = fixture.WorkspaceRemoteHTTPTest.setUp_local
    tearDown_local = fixture.WorkspaceRemoteHTTPTest.tearDown_local
    request = fixture.WorkspaceRemoteHTTPTest.request
    login = fixture.WorkspaceRemoteHTTPTest.login
    allocated = fixture.WorkspaceRemoteHTTPTest.allocated

    def second_server(self):
        config = self.manager.registry.server(self.sid)
        body = {k: config[k] for k in ('host', 'user', 'workspace', 'port', 'python')}
        return self.manager.save_server({**body, 'name': 'Other server'})['server']['id']

    @staticmethod
    def initialized(store, body):
        project = store.allocate(body)
        project['initialization'] = {'git': {'ok': True}, 'beads': {'ok': True}}
        write_json_atomic(store.meta(project['id']) / 'project.json', project)
        return {'project': project}

    def test_slow_check_and_initialization_allow_same_and_other_server_cancellation(self):
        _, same = self.allocated()
        _, other = self.allocated(self.second_server())
        for phase in ('check', 'initialization'):
            with self.subTest(phase=phase):
                started, release = threading.Event(), threading.Event()
                created, results, pending = [], {}, []

                def pause():
                    started.set()
                    if not release.wait(10):
                        raise RuntimeError('test release timed out')

                def checking(*args):
                    if phase == 'check':
                        pause()
                    return fixture.READY

                def initializing(store, body):
                    result = self.initialized(store, body)
                    pending.append(result['project']['id'])
                    if phase == 'initialization':
                        pause()
                    return result

                def creating():
                    created.append(self.request('/api/workspaces/create', {'name': 'Slow', 'server_id': self.sid}))

                def cancel(key, path, done):
                    try:
                        results[key] = self.request(path, {})
                    finally:
                        done.set()

                with patch.object(remote_worker, 'check', side_effect=checking), patch.object(ReferenceStore, 'create', initializing), \
                        patch.object(self.worker.app.workspaces.sessions, 'cancel', return_value={'ok': True}), \
                        patch.object(latex, 'cancel', return_value={'ok': True}):
                    creator = threading.Thread(target=creating)
                    callers = []
                    creator.start()
                    try:
                        self.assertTrue(started.wait(5))
                        for target, prefix in (('same', same), ('other', other)):
                            for kind, route in (('agent', '/sessions/abcdef/cancel'), ('latex', '/latex/cancel')):
                                done = threading.Event()
                                key = target + '-' + kind
                                thread = threading.Thread(target=cancel, args=(key, prefix + route, done))
                                callers.append((thread, done))
                                thread.start()
                        for thread, done in callers:
                            self.assertTrue(done.wait(2), 'cancellation was blocked by unrelated creation')
                        self.assertTrue(all(result[0] == 200 for result in results.values()), results)
                        if phase == 'initialization':
                            # Visible intent is not permission to delete an unfinished allocation.
                            prefix = '/api/workspaces/' + pending[0]
                            self.assertEqual(self.request(prefix + '/delete', {})[2]['code'], 'remote_busy')
                            with self.manager.connection(self.sid) as conn:
                                self.assertTrue(conn.json('GET', '/status')['busy'])
                                with self.assertRaises(WorkspaceError) as failure:
                                    conn.json('POST', prefix + '/delete', {})
                                self.assertEqual(failure.exception.code, 'remote_busy')
                    finally:
                        release.set()
                        creator.join(10)
                        for thread, done in callers:
                            thread.join(10)
                self.assertEqual(created[0][0], 200, created)

    def test_stalled_disconnect_probe_does_not_block_other_server_cancellation(self):
        _, same = self.allocated()
        _, other = self.allocated(self.second_server())
        self.assertEqual(self.request(same)[0], 200)
        conn = self.manager.connections[self.sid]
        started, release, cancelled = threading.Event(), threading.Event(), threading.Event()
        results = {}

        def stalled(*args):
            started.set()
            if not release.wait(10):
                raise RuntimeError('test release timed out')
            raise remote_transport.unavailable()

        def disconnecting():
            results['disconnect'] = self.request('/api/workspaces/servers/' + self.sid + '/disconnect', {})

        def cancelling():
            try:
                results['cancel'] = self.request(other + '/sessions/abcdef/cancel', {})
            finally:
                cancelled.set()

        with patch.object(conn, 'json', side_effect=stalled), patch.object(self.worker.app.workspaces.sessions, 'cancel', return_value={'ok': True}):
            transition = threading.Thread(target=disconnecting)
            cancel = threading.Thread(target=cancelling)
            transition.start()
            try:
                self.assertTrue(started.wait(5))
                cancel.start()
                self.assertTrue(cancelled.wait(2), 'healthy-host cancellation waited on a stalled disconnect')
                self.assertEqual(results['cancel'][0], 200)
                self.assertEqual(self.request(same)[2]['code'], 'remote_busy')
                self.assertEqual(self.request('/api/workspaces/servers')[0], 200)
            finally:
                release.set()
                transition.join(10)
                if cancel.ident:
                    cancel.join(10)
        self.assertEqual(results['disconnect'][0], 503)
        self.assertTrue(conn.connected, 'a failed busy probe must not kill unknown remote tasks')
        self.assertFalse(self.manager.registry.server(self.sid)['suspended'])
        self.assertEqual(self.request(same)[0], 200, 'failed transition must release its reservation')

    def test_creation_reservation_protects_target_across_registry_instances(self):
        other = Remotes(self.tmp.name)
        try:
            with self.manager.control(self.sid):
                for operation in (lambda: other.delete_server(self.sid),
                                  lambda: other.save_server({'host': 'different.example'}, self.sid)):
                    with self.assertRaises(WorkspaceError) as failure:
                        operation()
                    self.assertEqual(failure.exception.code, 'remote_busy')
            self.assertEqual(other.save_server({'name': 'Still valid'}, self.sid)['server']['name'], 'Still valid')
        finally:
            other.close()


if __name__ == '__main__':
    unittest.main()
