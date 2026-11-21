"""Current-project issue management through the native bd CLI only."""
from __future__ import annotations

import hashlib
import json
import re

from . import WorkspaceError
from .beads_cli import Client

LIMIT = 1000
TYPES = {'task', 'bug', 'feature', 'epic', 'chore', 'decision', 'spike', 'story', 'milestone'}
STATUSES = {'open', 'in_progress', 'blocked', 'deferred', 'closed'}


def _text(value, name, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or '\x00' in value or (required and not value.strip()):
        raise WorkspaceError('Invalid ' + name)
    return value


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,199}', value):
        raise WorkspaceError('Invalid Beads issue ID')
    return value


def _rows(data):
    if data is None:
        return []
    if not isinstance(data, list) or any(not isinstance(row, dict) or not isinstance(row.get('id'), str) for row in data):
        raise WorkspaceError('Unexpected Beads issue response', 502, 'beads_invalid_output')
    return data


def _local(client, issue_id):
    """No partial-ID expansion or cross-project fallback in native show/update."""
    issue_id = _id(issue_id)
    rows = _rows(client.run(['list', '--all', '--limit=0', '--id=' + issue_id]))
    if not any(row['id'] == issue_id for row in rows):
        raise WorkspaceError('Issue not found in the current project', 404, 'beads_issue_not_found')
    return issue_id


def _issue(client, issue_id):
    data = client.run(['show', '--', issue_id])
    rows = _rows(data if isinstance(data, list) else [data])
    if len(rows) != 1 or rows[0]['id'] != issue_id:
        raise WorkspaceError('Unexpected Beads issue response', 502, 'beads_invalid_output')
    issue = rows[0]
    # The native revision covers field changes within one timestamp second.
    # Older compatible builds may omit it; use the complete detail snapshot.
    if not issue.get('revision'):
        issue['revision'] = hashlib.sha256(json.dumps(issue, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return issue


def listing(root):
    client = Client(root)
    rows = _rows(client.run(['list', '--all', '--limit=' + str(LIMIT + 1)]))
    issues = rows[:LIMIT]
    ids = {row['id'] for row in issues}
    ready = _rows(client.run(['ready', '--limit=0']))
    blocked = _rows(client.run(['blocked']))
    return {'issues': issues, 'ready_ids': [row['id'] for row in ready if row['id'] in ids],
            'blocked_ids': [row['id'] for row in blocked if row['id'] in ids], 'truncated': len(rows) > LIMIT}


def show(root, issue_id):
    client = Client(root)
    issue_id = _local(client, issue_id)
    issue = _issue(client, issue_id)
    comments = client.run(['comments', '--', issue_id]) or []
    if not isinstance(comments, list):
        raise WorkspaceError('Unexpected Beads comments response', 502, 'beads_invalid_output')
    return {'issue': issue, 'comments': comments}


def _fields(body, create=False):
    flags = []
    if create or 'title' in body:
        flags.append('--title=' + _text(body.get('title'), 'title', 500, True).strip())
    for key, limit in (('description', 20000), ('assignee', 200)):
        if key in body:
            flags.append('--' + key + '=' + _text(body[key], key, limit))
    if create or 'issue_type' in body:
        kind = body.get('issue_type', 'task')
        if not isinstance(kind, str) or kind not in TYPES:
            raise WorkspaceError('Unsupported Beads issue type')
        flags.append('--type=' + kind)
    if create or 'priority' in body:
        priority = body.get('priority', 2)
        if type(priority) is not int or priority not in range(5):
            raise WorkspaceError('Priority must be an integer from 0 to 4')
        flags.append('--priority=' + str(priority))
    if 'labels' in body:
        labels = body['labels']
        if not isinstance(labels, list) or len(labels) > 30:
            raise WorkspaceError('At most 30 labels are supported')
        for label in labels:
            _text(label, 'label', 80, True)
            if any(char in label for char in ',\r\n"'):
                raise WorkspaceError('Labels cannot contain commas, quotes or newlines')
        flags.append(('--labels=' if create else '--set-labels=') + ','.join(dict.fromkeys(labels)))
    if 'status' in body:
        if not isinstance(body['status'], str) or body['status'] not in STATUSES:
            raise WorkspaceError('Unsupported Beads issue status')
        flags.append('--status=' + body['status'])
    return flags


def action(root, operation, body):
    editable = {'title', 'description', 'issue_type', 'priority', 'assignee', 'labels'}
    allowed = {'create': editable, 'update': editable | {'id', 'status', 'expected_revision'},
               'comment': {'id', 'text'}, 'dependency': {'id', 'depends_on', 'remove'}}
    if operation not in allowed:
        raise WorkspaceError('Route not found', 404)
    if set(body) - allowed[operation]:
        raise WorkspaceError('Unknown Beads request fields')
    if operation == 'create':
        flags = _fields(body, create=True)
        client = Client(root)
        data = client.run(['create', '--repo=.', *flags], write=True)
        if not isinstance(data, dict) or not data.get('id'):
            raise WorkspaceError('Task may have been created. Refresh before retrying.', 502, 'beads_invalid_output')
        return {'issue': data}
    issue_id = _id(body.get('id'))
    if operation == 'update':
        flags = _fields(body)
        expected = _text(body.get('expected_revision'), 'expected_revision', 200, True)
        if not flags:
            raise WorkspaceError('No task changes provided')
    elif operation == 'comment':
        text = _text(body.get('text'), 'comment', 10000, True)
    else:
        target = _id(body.get('depends_on'))
        if target == issue_id or type(body.get('remove', False)) is not bool:
            raise WorkspaceError('Invalid dependency')
    client = Client(root)
    _local(client, issue_id)
    if operation == 'update':
        current = _issue(client, issue_id)
        if expected != str(current['revision']):
            raise WorkspaceError('Task changed elsewhere. Reload it before saving; your edits have not been applied.', 409, 'beads_conflict')
        # One native mutation per request. Combining a status change with edits
        # uses bd update, which enforces close blockers without --force. A pure
        # close/reopen uses the corresponding native command and its events.
        if flags == ['--status=closed']:
            args = ['close', '--', issue_id]
        elif flags == ['--status=open'] and current['status'] == 'closed':
            args = ['reopen', '--', issue_id]
        else:
            args = ['update', *flags, '--', issue_id]
        client.run(args, write=True)
        # Do not turn an accepted write into a failure if a follow-up read locks.
        # The UI loads authoritative details separately; this is just an identity.
        return {'issue': {'id': issue_id}, 'ok': True}
    if operation == 'comment':
        client.run(['comments', 'add', '--', issue_id, text], write=True)
    else:
        _local(client, target)
        client.run(['dep', 'remove' if body.get('remove', False) else 'add', '--', issue_id, target], write=True)
    return {'ok': True}
