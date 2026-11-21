"""Bounded App Server integration using a real local fake stdio process, no model."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from easyread.workspaces import codex_protocol as protocol
from easyread.workspaces import codex_requests, session_cli as cli
from easyread.workspaces.sessions import Sessions, SessionError
from easyread.workspaces.store import Store


FAKE_SERVER = r'''
import json, sys, uuid
thread = None
turn = None
goal = None
background = False
queued = False
calls = []
replies = []
initialized = False

def emit(value):
    print(json.dumps(value), flush=True)

def notice(method, params):
    emit({'method': method, 'params': {'threadId': thread, **params}})

def start_turn():
    global turn
    turn = 'turn-' + str(len(calls))
    notice('turn/started', {'turn': {'id': turn, 'status': 'inProgress'}})

for line in sys.stdin:
    m = json.loads(line)
    method = m.get('method')
    if not method:
        replies.append(m)
        continue
    p = m.get('params', {})
    if method == 'initialized':
        initialized = True
        continue
    calls.append({'method': method, 'params': p})
    result = {}
    if method == 'initialize':
        assert p['capabilities']['experimentalApi']
        result = {'userAgent': 'fake/0.169.0'}
    elif method == 'config/read':
        result = {'config': {'model': 'fake-default', 'model_reasoning_effort': 'low'}}
    elif method == 'thread/start':
        assert initialized
        assert p['approvalPolicy'] == 'never' and p['sandbox'] == 'danger-full-access'
        thread = str(uuid.uuid4())
        result = {'thread': {'id': thread}}
        notice('thread/started', result)
    elif method == 'thread/resume':
        assert p['excludeTurns'] is True
        thread = p['threadId']
        result = {'thread': {'id': thread}}
    elif method == 'thread/read':
        assert p['includeTurns'] is False
        result = {'thread': {'id': thread, 'status': {'type': 'active' if turn else 'idle'}}}
    elif method == 'experimentalFeature/list':
        result = {'data': [{'name': n, 'enabled': True} for n in ['goals', 'default_mode_request_user_input']]}
    elif method == 'thread/goal/get':
        result = {'goal': goal}
    elif method == 'thread/goal/set':
        goal = {'objective': p.get('objective', (goal or {}).get('objective')), 'status': p['status'], 'threadId': thread}
        assert p['origin'] == 'user'
        notice('thread/goal/updated', {'goal': goal})
        result = {'goal': goal}
        if goal['status'] == 'active' and not turn:
            start_turn()
    elif method == 'thread/goal/clear':
        goal = None
        notice('thread/goal/cleared', {})
    elif method == 'turn/start':
        if turn:
            emit({'id': m['id'], 'error': {'code': -32602, 'message': 'already active'}})
            continue
        start_turn()
        result = {'turn': {'id': turn, 'status': 'inProgress'}}
    elif method == 'turn/steer':
        if p['expectedTurnId'] != turn:
            emit({'id': m['id'], 'error': {'code': -32602, 'message': 'stale turn'}})
            continue
        result = {'turnId': turn}
    elif method in ['turn/interrupt', '_test/complete']:
        notice('turn/completed', {'turn': {'id': turn, 'status': 'interrupted' if method == 'turn/interrupt' else 'completed'}})
        turn = None
    elif method == 'thread/backgroundTerminals/list':
        result = {'data': [{'processId': 'background-1'}] if background else []}
    elif method == 'thread/queue/list':
        result = {'data': [{'id': 'queued-1'}] if queued else []}
    elif method == '_test/queue':
        queued = p['active']
        notice('thread/queue/changed', {})
    elif method == 'skills/list':
        result = {'data': [{'cwd': p['cwds'][0], 'skills': [
            {'name': 'research', 'path': '/native/skills/research/SKILL.md', 'enabled': True},
            {'name': 'disabled', 'path': '/native/disabled/SKILL.md', 'enabled': False},
            {'name': 'ambiguous', 'path': '/native/one/SKILL.md', 'enabled': True},
            {'name': 'ambiguous', 'path': '/native/two/SKILL.md', 'enabled': True}]}]}
    elif method == '_test/background':
        background = p['active']
    elif method == '_test/rotate':
        turn = 'unobserved-new-turn'
    elif method == '_test/notify':
        notice(p['method'], p['params'])
    elif method == '_test/request':
        emit({'id': p['id'], 'method': p['method'], 'params': {'threadId': thread, 'turnId': turn, **p.get('params', {})}})
    elif method == '_test/calls':
        result = {'calls': calls, 'replies': replies}
    elif method == '_test/exit':
        sys.exit(3)
    else:
        emit({'id': m['id'], 'error': {'code': -32601, 'message': 'unsupported'}})
        continue
    emit({'id': m['id'], 'result': result})
'''

CAPABILITIES = {"app_server": True, "goal": True, "steer": True, "user_input": True,
                "approvals": True, "elicitation": True, "background_terminals": True,
                "methods": ["thread/start", "thread/resume", "thread/read", "turn/start", "turn/steer", "turn/interrupt",
                            "thread/goal/get", "thread/goal/set", "thread/goal/clear", "experimentalFeature/list",
                            "thread/backgroundTerminals/list", "thread/queue/list", "skills/list"]}


class WorkspaceCodexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="easyread-codex-fake-")
        self.base = Path(self.tmp.name)
        self.script = self.base / "fake_codex.py"
        self.script.write_text(FAKE_SERVER)
        self.store = Store(self.base / "library")
        self.pid = self.store.allocate({"name": "Disposable protocol fixture"})["id"]
        self.sessions = Sessions(self.store)
        self.patchers = [patch.dict(os.environ, {"CODEX_HOME": str(self.base / "empty-host"),
                                                "EASYREAD_WORKSPACE_CLI_ISOLATION": "1"}),
                         patch.object(protocol, "probe", side_effect=lambda *a: dict(CAPABILITIES)),
                         patch.object(cli, "executable", return_value=sys.executable)]
        real = protocol.AppServer
        def app_server(argv, cwd, env, on_message, on_exit):
            self.assertEqual(argv[1:4], ["app-server", "--listen", "stdio://"])
            self.assertIn("mcp_servers.easyread_project.command", " ".join(argv))
            return real([sys.executable, str(self.script)], cwd, env, on_message, on_exit)
        self.patchers.append(patch.object(protocol, "AppServer", side_effect=app_server))
        for p in self.patchers:
            p.start()
        self.addCleanup(self.tmp.cleanup)
        for p in self.patchers:
            self.addCleanup(p.stop)
        self.addCleanup(self.sessions.close)
        self.sid = self.sessions.create(self.pid, {"engine": "codex"})["session"]["id"]

    def until(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(.01)
        self.fail("Fake native state did not settle within three seconds")

    def snapshot(self):
        return self.sessions.get(self.pid, self.sid)

    def runtime(self):
        return self.sessions.native.runtimes[(self.pid, self.sid)]

    def rpc(self, method, params=None):
        return self.runtime().rpc.call(method, params or {})

    def start(self):
        self.sessions.send(self.pid, self.sid, {"message": "fake chat"})
        return self.until(lambda: self.snapshot()["session"].get("active_turn_id"))

    def notify(self, method, params):
        self.rpc("_test/notify", {"method": method, "params": params})

    def request(self, rid, method="item/tool/requestUserInput", **params):
        params = params or {"isBlocking": True, "itemId": "question-item", "questions": [
            {"id": "q", "header": "Pick", "question": "Which dataset?", "isOther": True, "isSecret": False}]}
        self.rpc("_test/request", {"id": rid, "method": method, "params": params})
        return self.until(lambda: next((p for p in self.snapshot()["session"]["pending_requests"]
                                       if type(p["id"]) is type(rid) and p["id"] == rid), None))

    def test_plain_skill_is_long_running_without_goal_timer_or_synthetic_continuation(self):
        host = self.base / 'empty-host'
        (host / 'skills').mkdir(parents=True)
        (host / 'plugins').mkdir()
        prompt = '$research run the long experiment'
        with patch('easyread.workspaces.sessions.threading.Timer', side_effect=AssertionError('No ordinary-turn deadline')):
            self.sessions.send(self.pid, self.sid, {'message': prompt})
        self.until(lambda: self.runtime().active_turn)
        home = cli.project_home(self.store.meta(self.pid), 'codex')
        self.assertTrue((home / 'skills').is_dir())
        self.assertTrue((home / 'plugins').is_dir())
        calls = self.rpc('_test/calls')['calls']
        sent = next(c['params']['input'] for c in calls if c['method'] == 'turn/start')
        self.assertEqual(sent, [{'type': 'text', 'text': prompt},
                                {'type': 'skill', 'name': 'research', 'path': '/native/skills/research/SKILL.md'}])
        self.assertFalse(any(c['method'] == 'thread/goal/set' for c in calls))
        self.assertEqual(self.runtime().inputs('$HOME is a shell variable'), [{'type': 'text', 'text': '$HOME is a shell variable'}])
        for name, code in [('disabled', 'skill_unavailable'), ('ambiguous', 'ambiguous_skill')]:
            with self.assertRaises(SessionError) as error:
                self.runtime().inputs('$' + name + ' test')
            self.assertEqual(error.exception.code, code)
        self.runtime().last_activity = -10**9
        self.sessions.native.reap()
        self.assertTrue(self.sessions.busy(self.pid))
        self.rpc('_test/complete')
        self.until(lambda: not self.runtime().active_turn)
        self.sessions.native.reap()
        calls = self.rpc('_test/calls')['calls']
        self.assertEqual(sum(c['method'] == 'turn/start' for c in calls), 1)
        self.assertFalse(any(c['method'] == 'thread/goal/set' for c in calls))
        self.notify('thread/status/changed', {'status': {'type': 'active', 'activeFlags': []}})
        self.until(lambda: self.runtime().native_active)
        self.runtime().last_activity = -10**9
        self.sessions.native.reap()
        self.assertTrue(self.sessions.busy(self.pid))
        self.notify('thread/status/changed', {'status': {'type': 'idle'}})
        self.until(lambda: not self.runtime().native_active)
        self.rpc('_test/queue', {'active': True})
        self.until(lambda: self.runtime().queued)
        self.runtime().last_activity = -10**9
        self.sessions.native.reap()
        self.assertTrue(self.sessions.native.loaded(self.pid, self.sid))
        self.assertEqual(self.snapshot()['session']['state'], 'queued')
        with self.assertRaises(SessionError):
            self.sessions.send(self.pid, self.sid, {'message': 'must not bypass existing native queue'})
        revision = self.runtime().queue_revision
        self.rpc('_test/queue', {'active': False})
        self.until(lambda: self.runtime().queue_revision > revision)
        self.sessions.native.reap()
        self.assertFalse(self.runtime().queued)

    def idle_runtime(self):
        self.start()
        self.sessions.native.stop_event.set()
        self.sessions.native.reaper.join(3)
        self.assertFalse(self.sessions.native.reaper.is_alive())
        self.rpc('_test/complete')
        self.until(lambda: not self.runtime().active_turn)
        self.sessions.native.reap()
        self.assertFalse(self.runtime().busy())
        return self.runtime()

    def assert_idle_cleanup_rechecks_after_send(self, cleanup):
        runtime = self.idle_runtime()
        checked, release = threading.Event(), threading.Event()
        original = self.sessions.native.stop
        errors = []
        def delayed_stop(*args, **kwargs):
            checked.set()
            if not release.wait(3):
                raise AssertionError('Idle cleanup was not released')
            return original(*args, **kwargs)
        def clean():
            try:
                cleanup()
            except Exception as error:
                errors.append(error)
        with patch.object(self.sessions.native, 'stop', side_effect=delayed_stop):
            worker = threading.Thread(target=clean, name='idle-cleanup-test')
            worker.start()
            try:
                self.assertTrue(checked.wait(1))
                self.sessions.send(self.pid, self.sid, {'message': 'new experiment after the idle check'})
                turn_id = self.until(lambda: runtime.active_turn)
            finally:
                release.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertIsNone(runtime.rpc.proc.poll(), 'Idle cleanup killed newly started work')
        self.assertEqual(runtime.active_turn, turn_id)
        self.assertIs(self.runtime(), runtime)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], SessionError)
        self.assertEqual((errors[0].code, errors[0].status), ('conflict', 409))
        self.assertEqual(self.snapshot()['session']['messages'][-1]['content'], 'new experiment after the idle check')

    def test_export_cleanup_rechecks_idle_after_concurrent_send(self):
        self.assert_idle_cleanup_rechecks_after_send(lambda: self.sessions.prepare_export(self.pid))

    def test_terminal_handoff_reserves_session_before_idle_shutdown(self):
        runtime = self.idle_runtime()
        entered, release = threading.Event(), threading.Event()
        errors = []
        original = self.sessions.native.stop
        def stop(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError('Terminal shutdown was not released')
            return original(*args, **kwargs)
        def open_terminal():
            try:
                self.sessions.terminal(self.pid, self.sid)
            except Exception as error:
                errors.append(error)
        with patch('easyread.workspaces.session_terminal.unavailable_reason', return_value=''), \
                patch.object(self.sessions.native, 'stop', side_effect=stop), \
                patch.object(cli, 'transcript', return_value=self.base / 'fake-transcript'), \
                patch.object(cli, 'read_transcript', return_value=''), \
                patch('easyread.workspaces.codex_model_list.defaults', return_value=('fake-default', 'low')), \
                patch.object(self.sessions.terminals, 'open_native', return_value={'url': '/workspace-terminal/fake/'}) as opened:
            worker = threading.Thread(target=open_terminal)
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                with self.assertRaises(SessionError) as error:
                    self.sessions.send(self.pid, self.sid, {'message': 'cannot race terminal handoff'})
                self.assertEqual(error.exception.status, 409)
                self.assertEqual(self.snapshot()['session']['state'], 'idle')
            finally:
                release.set(); worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(errors)
        opened.assert_called_once()
        self.assertIsNotNone(runtime.rpc.proc.poll())

    def test_effort_settings_release_idle_runtime_and_resume_identity_for_native_goal(self):
        from easyread.workspaces import model_catalog, session_reasoning
        with patch.object(model_catalog, 'listing', return_value={
                'models': [], 'default': '', 'reasoning_levels': list(session_reasoning.LEVELS['codex'])}):
            self.sessions.settings(self.pid, self.sid, {'model': 'custom-one', 'reasoning_effort': 'high'})
            runtime = self.idle_runtime()
            calls = self.rpc('_test/calls')['calls']
            started = next(c['params'] for c in calls if c['method'] == 'thread/start')
            self.assertEqual(started['config'], {'model_reasoning_effort': 'high'})
            identity = self.snapshot()['session']['native_id']
            old_messages = self.snapshot()['session']['messages']
            updated = self.sessions.settings(self.pid, self.sid, {'model': 'custom-two', 'reasoning_effort': 'xhigh'})
            self.assertEqual(updated['session']['native_id'], identity)
            self.assertEqual(updated['session']['messages'], old_messages)
            self.assertIsNotNone(runtime.rpc.proc.poll())
            self.assertFalse(self.sessions.native.loaded(self.pid, self.sid))
            self.sessions.goal(self.pid, self.sid, {'action': 'set', 'objective': 'native continuation'})
            self.until(lambda: self.runtime().active_turn)
            calls = self.rpc('_test/calls')['calls']
            resumed = next(c['params'] for c in calls if c['method'] == 'thread/resume')
            self.assertEqual(resumed['threadId'], identity)
            self.assertEqual(resumed['model'], 'custom-two')
            self.assertEqual(resumed['config'], {'model_reasoning_effort': 'xhigh'})
            self.assertFalse(any(c['method'] == 'turn/start' for c in calls))
            with patch.object(self.sessions.native, 'stop') as stop:
                with self.assertRaises(SessionError):
                    self.sessions.settings(self.pid, self.sid, {'reasoning_effort': ''})
                stop.assert_not_called()
            self.sessions.goal(self.pid, self.sid, {'action': 'clear'})
            self.rpc('_test/complete')
            self.notify('thread/status/changed', {'status': {'type': 'idle'}})
            self.until(lambda: not self.runtime().active_turn and not self.runtime().native_active)
            self.sessions.native.reap()
            self.sessions.settings(self.pid, self.sid, {'model': '', 'reasoning_effort': ''})
            self.sessions.send(self.pid, self.sid, {'message': 'inherit actual defaults'})
            calls = self.rpc('_test/calls')['calls']
            resumed = next(c['params'] for c in calls if c['method'] == 'thread/resume')
            self.assertEqual(resumed['model'], 'fake-default')
            self.assertEqual(resumed['config'], {'model_reasoning_effort': 'low'})
            snapshot = self.snapshot()['session']
            self.assertEqual((snapshot['model'], snapshot['reasoning_effort']), ('', ''))
            self.assertEqual(snapshot['native_id'], identity)

    def test_accepted_turn_retains_admission_while_notifications_are_delayed(self):
        runtime = self.idle_runtime()
        self.sessions.native.max_active = 1
        second = self.sessions.create(self.pid, {'engine': 'codex'})['session']['id']
        entered, release = threading.Event(), threading.Event()
        original = runtime.rpc.on_message
        def delayed_notification(message):
            if message.get('method') == 'turn/started':
                entered.set()
                if not release.wait(3):
                    raise AssertionError('Native notification was not released')
            original(message)
        with patch.object(runtime.rpc, 'on_message', side_effect=delayed_notification):
            try:
                result = self.sessions.send(self.pid, self.sid, {'message': 'accepted before notification processing'})
                self.assertTrue(entered.wait(1))
                self.assertEqual(result['session']['state'], 'queued')
                self.assertEqual(self.sessions.native.count_active(), 1)
                with self.assertRaises(SessionError) as other:
                    self.sessions.send(self.pid, second, {'message': 'must not exceed active capacity'})
                self.assertEqual((other.exception.code, other.exception.status), ('active_capacity', 429))
                with self.assertRaises(SessionError) as same:
                    self.sessions.send(self.pid, self.sid, {'message': 'must not submit a duplicate turn'})
                self.assertEqual((same.exception.code, same.exception.status), ('conflict', 409))
                calls = self.rpc('_test/calls')['calls']
                self.assertEqual(sum(c['method'] == 'turn/start' for c in calls), 2)
                runtime.last_activity = -10**9
                self.sessions.native.reap()
                self.assertIsNone(runtime.rpc.proc.poll())
                self.assertEqual(self.sessions.native.count_active(), 1)
            finally:
                release.set()
        self.until(lambda: runtime.active_turn)
        self.rpc('_test/complete')
        self.until(lambda: not runtime.active_turn)
        self.sessions.native.reap()
        self.assertEqual(self.sessions.native.count_active(), 0)

    def test_goal_pause_reconciles_active_turn_ahead_of_older_idle_notification(self):
        runtime = self.idle_runtime()
        idle_entered, turn_entered = threading.Event(), threading.Event()
        release_idle, release_turn = threading.Event(), threading.Event()
        original = runtime.rpc.on_message
        def delayed(message):
            method = message.get('method')
            if method == 'thread/status/changed' and not idle_entered.is_set():
                idle_entered.set()
                if not release_idle.wait(3):
                    raise AssertionError('Old idle notification was not released')
            if method == 'turn/started':
                turn_entered.set()
                if not release_turn.wait(3):
                    raise AssertionError('Goal turn notification was not released')
            original(message)
        with patch.object(runtime.rpc, 'on_message', side_effect=delayed):
            try:
                self.notify('thread/status/changed', {'status': {'type': 'idle'}})
                self.assertTrue(idle_entered.wait(1))
                self.sessions.goal(self.pid, self.sid, {'action': 'set', 'objective': 'fake native goal'})
                self.sessions.goal(self.pid, self.sid, {'action': 'pause'})
                self.assertTrue(runtime.native_active)
                self.assertIsNone(runtime.active_turn)
                release_idle.set()
                self.assertTrue(turn_entered.wait(1))
                self.assertTrue(runtime.native_active, 'Older queued idle notification undid the metadata observation')
                runtime.last_activity = -10**9
                self.sessions.native.reap()
                self.assertTrue(runtime.busy())
                self.assertIsNone(runtime.rpc.proc.poll())
            finally:
                release_idle.set()
                release_turn.set()
        self.until(lambda: runtime.active_turn and (runtime.goal or {}).get('status') == 'paused')
        self.rpc('_test/complete')
        self.notify('thread/status/changed', {'status': {'type': 'idle'}})
        self.until(lambda: not runtime.active_turn and not runtime.native_active)
        self.sessions.native.reap()
        self.assertFalse(runtime.busy())
        calls = self.rpc('_test/calls')['calls']
        reads = [c for c in calls if c['method'] == 'thread/read']
        self.assertEqual(len(reads), 1)
        self.assertIs(reads[0]['params']['includeTurns'], False)
        self.assertEqual(sum(c['method'] == 'turn/start' for c in calls), 1)

    def test_protocol_queues_apply_byte_backpressure_without_truncation(self):
        queue = protocol.ByteQueue(max_items=256, max_bytes=64)
        first, second = b'a' * 40, b'b' * 40
        queue.put(first, size=len(first))
        entered, finished = threading.Event(), threading.Event()
        errors = []
        def produce():
            entered.set()
            try:
                queue.put(second, size=len(second), timeout=1)
            except Exception as error:
                errors.append(error)
            finally:
                finished.set()
        worker = threading.Thread(target=produce)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertFalse(finished.wait(.05), 'Item count alone admitted more bytes than the budget')
            self.assertEqual(queue.queued_bytes, 40)
            self.assertEqual(queue.get(timeout=1), first)
            self.assertTrue(finished.wait(1))
            self.assertFalse(errors)
            self.assertEqual(queue.queued_bytes, 40)
            self.assertEqual(queue.get(timeout=1), second)
            self.assertEqual(queue.queued_bytes, 0)
        finally:
            worker.join(2)
        self.assertFalse(worker.is_alive())

    def test_pending_request_byte_budget_fails_closed_and_releases_resolved_bytes(self):
        from easyread.workspaces import codex_runtime
        self.start()
        self.request('released')
        runtime = self.runtime()
        self.assertGreater(runtime.pending_bytes, 0)
        self.notify('serverRequest/resolved', {'requestId': 'released'})
        self.until(lambda: not runtime.pending)
        self.assertEqual(runtime.pending_bytes, 0)
        self.request('retained')
        budget = runtime.pending_bytes
        with patch.object(codex_runtime, 'MAX_PENDING_BYTES', budget), \
                patch.object(runtime.rpc, 'reply', wraps=runtime.rpc.reply) as reply:
            try:
                self.rpc('_test/request', {'id': 'excess', 'method': 'item/tool/requestUserInput', 'params': {
                    'isBlocking': True, 'itemId': 'extra', 'questions': [{'id': 'q', 'header': 'Choose', 'question': 'Another question'}]}})
            except SessionError:
                pass  # EOF may precede the fake request injection acknowledgement.
            self.until(lambda: self.snapshot()['session']['state'] == 'interrupted')
            self.until(lambda: runtime.rpc.proc.poll() is not None)
            reply.assert_not_called()
        self.assertIn('pending request budget', self.snapshot()['session']['error'])
        self.assertEqual(runtime.pending_bytes, budget)
        self.assertNotIn(protocol.request_key('excess'), runtime.pending)
        self.sessions.cancel(self.pid, self.sid)
        self.assertFalse(runtime.rpc.reader.is_alive())
        self.assertFalse(runtime.rpc.consumer.is_alive())
        self.assertFalse(runtime.rpc.writer.is_alive())

    def test_goal_is_native_no_duplicate_turn_and_browser_independent(self):
        self.sessions.send(self.pid, self.sid, {"message": "Research one objective", "mode": "goal"})
        self.until(lambda: self.snapshot()["session"].get("active_turn_id"))
        calls = self.rpc("_test/calls")["calls"]
        self.assertEqual(sum(c["method"] == "thread/goal/set" for c in calls), 1)
        self.assertFalse(any(c["method"] == "turn/start" for c in calls))
        self.rpc("_test/complete")
        self.until(lambda: self.snapshot()["session"].get("active_turn_id") is None)
        self.runtime().last_activity = -10**9  # Simulated multi-day absence, no browser heartbeat.
        self.sessions.native.reap()
        self.assertTrue(self.sessions.busy(self.pid))
        self.assertEqual(self.snapshot()["session"]["state"], "running")
        with self.assertRaises(SessionError):
            self.sessions.send(self.pid, self.sid, {"message": "must not start a conflicting turn"})
        self.sessions.goal(self.pid, self.sid, {"action": "pause"})
        self.assertEqual(self.snapshot()["session"]["goal"]["status"], "paused")
        self.sessions.goal(self.pid, self.sid, {"action": "resume"})
        self.until(lambda: (self.runtime().goal or {}).get('status') == 'active')
        with patch.object(self.runtime(), 'reconcile_status', side_effect=SessionError('rpc_uncertain', 'Cannot reconcile', 409)) as reconcile:
            self.sessions.cancel(self.pid, self.sid)
            reconcile.assert_called_once()
        self.assertFalse(self.sessions.native.loaded(self.pid))

    def test_exact_request_ids_questions_permissions_elicitation_and_stale_reply(self):
        turn = self.start()
        number = self.request(7)
        string = self.request("7", isBlocking=False, itemId="nonblocking", questions=[
            {"id": "q", "header": "Other", "question": "Extra context?", "isSecret": True}])
        self.assertTrue(number["is_blocking"])
        self.assertFalse(string["is_blocking"])
        generation = number["generation"]
        with self.assertRaises(SessionError):
            self.sessions.respond(self.pid, self.sid, {"request_id": 7, "generation": "stale", "answers": {"q": {"answers": ["x"]}}})
        self.sessions.respond(self.pid, self.sid, {"request_id": 7, "generation": generation, "answers": {"q": {"answers": ["dataset-a"]}}})
        self.assertEqual(self.snapshot()["session"]["pending_count"], 1)
        self.notify("serverRequest/resolved", {"requestId": "7"})
        self.until(lambda: not self.snapshot()["session"]["pending_count"])
        with self.assertRaises(SessionError):
            self.sessions.respond(self.pid, self.sid, {"request_id": "7", "generation": generation, "answers": {"q": {"answers": ["late"]}}})
        permissions = self.request("permissions", "item/permissions/requestApproval", permissions={"network": {"enabled": True}}, itemId="permissions")
        self.sessions.respond(self.pid, self.sid, {"request_id": permissions["id"], "generation": generation, "decision": "accept", "values": {"evil": "ignored"}})
        form = self.request("form", "mcpServer/elicitation/request", serverName="test", mode="form", message="Choose", requestedSchema={
            "type": "object", "properties": {"dataset": {"type": "string", "enum": ["a", "b"]}}, "required": ["dataset"]})
        self.sessions.respond(self.pid, self.sid, {"request_id": form["id"], "generation": generation, "decision": "accept", "values": {"dataset": "b"}})
        replies = self.rpc("_test/calls")["replies"]
        self.assertEqual(replies[0], {"id": 7, "result": {"answers": {"q": {"answers": ["dataset-a"]}}}})
        self.assertEqual(replies[1]["result"], {"permissions": {"network": {"enabled": True}}, "scope": "turn"})
        self.assertEqual(replies[2]["result"], {"action": "accept", "content": {"dataset": "b"}})
        self.assertEqual(number["turn_id"], turn)

    def test_steer_pins_observed_turn_and_native_stale_failure_never_starts_turn(self):
        turn = self.start()
        self.sessions.steer(self.pid, self.sid, {"message": "use small sample", "expected_turn_id": turn})
        self.rpc("_test/rotate")
        with self.assertRaises(SessionError):
            self.sessions.steer(self.pid, self.sid, {"message": "stale steer", "expected_turn_id": turn})
        calls = self.rpc("_test/calls")["calls"]
        self.assertEqual(sum(c["method"] == "turn/start" for c in calls), 1)
        self.assertEqual([c["params"]["expectedTurnId"] for c in calls if c["method"] == "turn/steer"], [turn, turn])
        self.assertNotIn("stale steer", [m["content"] for m in self.snapshot()["session"]["messages"]])

    def test_admission_pending_background_tools_and_idle_identity_resume(self):
        self.sessions.native.max_active = 1
        self.start()
        second = self.sessions.create(self.pid, {"engine": "codex"})["session"]["id"]
        with self.assertRaises(SessionError) as error:
            self.sessions.send(self.pid, second, {"message": "over capacity"})
        self.assertEqual((error.exception.code, error.exception.status), ("active_capacity", 429))
        pending = self.request("keep")
        self.rpc("_test/complete")
        self.runtime().last_activity = -10**9
        self.sessions.native.reap()
        self.assertTrue(self.sessions.native.loaded(self.pid, self.sid))
        self.notify("serverRequest/resolved", {"requestId": pending["id"]})
        self.until(lambda: not self.snapshot()["session"]["pending_count"])
        self.rpc("_test/background", {"active": True})
        self.sessions.native.reap()
        self.assertTrue(self.sessions.busy(self.pid))
        self.rpc("_test/background", {"active": False})
        self.sessions.native.reap()
        native_id = self.snapshot()["session"]["native_id"]
        self.runtime().last_activity = -10**9
        self.sessions.native.reap()
        self.assertFalse(self.sessions.native.loaded(self.pid, self.sid))
        self.assertEqual(self.snapshot()["session"]["runtime_state"], "sleeping")
        self.start()
        calls = self.rpc("_test/calls")["calls"]
        self.assertTrue(any(c["method"] == "thread/resume" and c["params"]["threadId"] == native_id for c in calls))
        self.assertFalse(any(c["method"] == "thread/start" for c in calls))
        self.assertEqual(self.snapshot()["session"]["native_id"], native_id)

    def test_runtime_exit_expires_prompts_and_get_never_resurrects(self):
        self.start()
        pending = self.request("restart")
        with self.assertRaises(SessionError):
            self.rpc("_test/exit")
        self.until(lambda: self.snapshot()["session"]["state"] == "interrupted")
        self.assertTrue(self.snapshot()["session"]["pending_requests"][0]["expired"])
        self.assertTrue(self.snapshot()["session"]["reconciliation_required"])
        with self.assertRaises(SessionError):
            self.sessions.respond(self.pid, self.sid, {"request_id": pending["id"], "generation": pending["generation"], "answers": {"q": {"answers": ["late"]}}})
        self.sessions.cancel(self.pid, self.sid)
        restarted = Sessions(self.store)
        try:
            with patch.object(protocol, "AppServer", side_effect=AssertionError("GET must not launch")):
                self.assertEqual(restarted.get(self.pid, self.sid)["session"]["state"], "interrupted")
                restarted.listing(self.pid)
        finally:
            restarted.close()

    def test_bounded_stream_upserts_and_no_project_lock_during_rpc(self):
        self.start()
        original = self.runtime().rpc.call
        entered, release = threading.Event(), threading.Event()
        def waiting(method, params, **kwargs):
            if method == "turn/steer":
                entered.set()
                release.wait(3)
            return original(method, params, **kwargs)
        errors = []
        def steer():
            try:
                self.sessions.steer(self.pid, self.sid, {"message": "waiting RPC"})
            except Exception as error:
                errors.append(error)
        with patch.object(self.runtime().rpc, "call", side_effect=waiting):
            worker = threading.Thread(target=steer)
            worker.start()
            self.assertTrue(entered.wait(1))
            acquired = self.store.lock(self.pid).thread.acquire(timeout=.5)
            try:
                self.assertTrue(acquired, "native wait held the project lock")
                self.snapshot()
            finally:
                if acquired:
                    self.store.lock(self.pid).thread.release()
                release.set()
                worker.join(3)
        self.assertFalse(errors)
        cursor = self.snapshot()["cursor"]
        turn = self.runtime().active_turn
        self.notify("item/agentMessage/delta", {"itemId": "answer", "turnId": turn, "delta": "a" * 210000})
        self.notify("item/completed", {"turnId": turn, "item": {"id": "answer", "type": "agentMessage", "text": "a" * 210000}})
        self.until(lambda: len([m for m in self.snapshot()["session"]["messages"] if m["role"] == "assistant"]) == 2)
        updates = self.sessions.updates(self.pid, self.sid, cursor, 100)
        self.assertEqual(sum(len(m["content"]) for m in updates["messages"] if m["role"] == "assistant"), 210000)
        self.assertTrue(all(len(m["content"]) <= 200000 for m in updates["messages"]))
        self.assertEqual(len({m["id"] for m in updates["messages"]}), len(updates["messages"]))

    def test_descendant_work_and_approvals_are_retained_not_auto_answered(self):
        turn = self.start()
        self.notify('item/completed', {'turnId': turn, 'item': {
            'id': 'spawn', 'type': 'collabAgentToolCall', 'tool': 'spawnAgent',
            'receiverThreadIds': ['native-child'], 'agentsStates': {'native-child': {'status': 'running'}}}})
        self.until(lambda: 'native-child' in self.runtime().children)
        approval = self.request('child-approval', 'item/commandExecution/requestApproval',
                                threadId='native-child', turnId='child-turn', itemId='shell', command='run experiment', reason='Needs approval')
        self.assertEqual(approval['thread_id'], 'native-child')
        self.assertEqual(self.rpc('_test/calls')['replies'], [])
        self.sessions.respond(self.pid, self.sid, {'request_id': approval['id'], 'generation': approval['generation'], 'decision': 'decline'})
        self.assertEqual(self.rpc('_test/calls')['replies'][-1]['result'], {'decision': 'decline'})
        self.rpc('_test/complete')
        self.until(lambda: not self.runtime().active_turn)
        self.runtime().last_activity = -10**9
        self.sessions.native.reap()
        self.assertTrue(self.sessions.native.loaded(self.pid, self.sid))
        self.assertTrue(self.sessions.busy(self.pid))

    def test_uncertain_mutation_reserves_capacity_and_never_retries(self):
        from easyread.workspaces.codex_runtime import Runtime
        # Intercept after handshake/thread creation; simulate a lost turn/start reply.
        real_call = Runtime.call
        def call(runtime, method, params):
            if method == 'turn/start':
                def lost(*args, **kwargs):
                    raise SessionError('rpc_uncertain', 'Accepted outcome unknown', 409)
                with patch.object(runtime.rpc, 'call', side_effect=lost):
                    return real_call(runtime, method, params)
            return real_call(runtime, method, params)
        with patch.object(Runtime, 'call', call):
            with self.assertRaises(SessionError):
                self.sessions.send(self.pid, self.sid, {'message': 'may have submitted an experiment'})
        self.assertTrue(self.runtime().uncertain)
        self.assertTrue(self.snapshot()['session']['reconciliation_required'])
        with self.assertRaises(SessionError):
            self.sessions.send(self.pid, self.sid, {'message': 'do not automatically retry'})
        self.assertTrue(self.sessions.busy(self.pid))
        self.sessions.cancel(self.pid, self.sid)
        self.assertFalse(self.sessions.native.loaded(self.pid, self.sid))

    def test_claude_native_capability_error_and_unsupported_form_decline(self):
        sid = self.sessions.create(self.pid, {"engine": "claude"})["session"]["id"]
        for action in (lambda: self.sessions.goal(self.pid, sid, {"action": "set", "objective": "x"}),
                       lambda: self.sessions.send(self.pid, sid, {"message": "/goal x"})):
            with self.assertRaises(SessionError) as error:
                action()
            self.assertEqual(error.exception.code, "capability_unavailable")
        params = {"mode": "openai/userVerification", "challenge": "secret"}
        with self.assertRaises(SessionError):
            codex_requests.response("mcpServer/elicitation/request", params, {"decision": "accept"})
        self.assertEqual(codex_requests.response("mcpServer/elicitation/request", params, {"decision": "decline"}), {"action": "decline"})


if __name__ == "__main__":
    unittest.main()
