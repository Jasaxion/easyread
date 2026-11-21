"""Fixed startup diagnostics, never reflected SSH/proxy/worker stderr."""
from . import WorkspaceError

MESSAGES = {
    'remote_ssh_client': 'OpenSSH could not start on the EasyRead deployment host. Install/check its ssh executable as the service account.',
    'remote_dns': 'SSH could not resolve the host. Check the alias and HostName in the deployment service account SSH config, or use a reachable IP address.',
    'remote_network': 'SSH could not establish the network connection. Check HostName, Port, VPN/firewall and any configured jump/proxy hosts.',
    'remote_host_key': 'SSH host-key verification failed. Independently verify the host fingerprint and correct known_hosts for the deployment service account (including jump hosts); keys are never accepted automatically.',
    'remote_auth': 'SSH key authentication failed. Check the effective SSH User, authorized_keys and the service account IdentityFile/ssh-agent. Password and passphrase prompts are disabled.',
    'remote_python': 'SSH reached the remote command but the configured Python is unavailable, not executable or older than 3.10.',
    'remote_worker_missing': 'SSH reached Python, but this interpreter cannot find the EasyRead worker package.',
    'remote_dependencies': 'SSH reached Python, but the EasyRead runtime dependencies are missing or cannot be imported.',
    'remote_protocol': 'The remote EasyRead worker protocol or workspace build does not match. Install the exact same reviewed EasyRead source as the deployment backend.',
    'remote_busy': 'This remote workspace is owned by another EasyRead worker. Disconnect that client first.',
    'remote_worker_failed': 'SSH did not produce a ready EasyRead worker. Check the remote Python/worker installation and service-account SSH configuration using the setup guide; raw remote logs are not exposed.',
    'remote_start_timeout': 'SSH/worker startup timed out. Check network/jump-host access and the remote Python/worker startup. No operation was retried.',
}
PYTHON_CODES = {'remote_python', 'remote_worker_missing', 'remote_dependencies', 'remote_protocol', 'remote_worker_failed'}


def startup_error(code, config):
    if code not in MESSAGES:
        code = 'remote_worker_failed'
    message = MESSAGES[code]
    if code in PYTHON_CODES:
        # Configuration is validated, but long absolute paths still need bounds.
        python = config['python'][:200]
        message += f' Configured Python: {python}. Install matching EasyRead and its dependencies in that interpreter; use the absolute venv/bin/python path in the server form. Nothing is installed automatically.'
    return WorkspaceError(message, 409 if code == 'remote_busy' else 503, code)


def ssh_failure_code(stderr, returncode=None):
    """Classify bounded English OpenSSH output without returning any of it."""
    text = stderr.lower()
    if any(s in text for s in (b'host key verification failed', b'remote host identification has changed',
                                b'host key is known for', b'host key has changed')):
        return 'remote_host_key'
    if any(s in text for s in (b'could not resolve hostname', b'name or service not known',
                                b'nodename nor servname provided', b'temporary failure in name resolution')):
        return 'remote_dns'
    if any(s in text for s in (b'permission denied (', b'authentication failed', b'too many authentication failures',
                                b'no supported authentication methods available', b'load key ')):
        return 'remote_auth'
    if any(s in text for s in (b'connection refused', b'connection timed out', b'operation timed out',
                                b'no route to host', b'network is unreachable', b'connection reset',
                                b'connection closed', b'could not request local forwarding', b'address already in use')):
        return 'remote_network'
    if returncode in (126, 127):
        return 'remote_python'
    return 'remote_worker_failed'
