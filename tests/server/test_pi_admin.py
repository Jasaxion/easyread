"""Pi administrative commands: explicit npm installation and redacted auth checks."""
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from easyread.server import terminal

spec = importlib.util.spec_from_file_location('pi_admin', Path(__file__).resolve().parents[2] / 'docker/cli.py')
admin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admin)


class PiAdminTest(unittest.TestCase):
    def invoke(self, *args):
        with patch('sys.argv', ['easyread-cli', *args]), patch('easyread.app.proxy.apply_saved'), contextlib.redirect_stdout(io.StringIO()):
            admin.main()

    def test_install_and_update_use_latest_npm_without_lifecycle_scripts(self):
        for action in ('install', 'update'):
            with self.subTest(action=action), patch.object(admin.subprocess, 'run') as run:
                self.invoke(action, 'pi')
                self.assertEqual(run.call_args_list[0].args[0], [
                    'npm', 'install', '--global', '--ignore-scripts', '@earendil-works/pi-coding-agent@latest'])
                self.assertEqual(run.call_args_list[1].args[0], ['pi', '--version'])

    def test_login_opens_native_pi_without_creating_session(self):
        with patch.object(admin.os, 'execvp') as execute:
            self.invoke('login', 'pi')
        execute.assert_called_once_with('pi', ['pi', '--no-session'])

    def test_auth_check_never_prints_credentials_or_refreshes(self):
        with patch.object(admin.subprocess, 'run', return_value=SimpleNamespace(returncode=1)) as run:
            with self.assertRaises(SystemExit) as exit:
                self.invoke('auth-status', 'pi', '--provider', 'anthropic')
        self.assertEqual(exit.exception.code, 1)
        run.assert_called_once_with(['pi', 'auth', 'check', '--provider', 'anthropic', '--json', '--no-refresh'], timeout=30)

    def test_default_provider_and_unconfigured_status(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(admin.os.environ, {'PI_CODING_AGENT_DIR': root}):
            with patch.object(admin.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()) as output:
                admin.pi_status()
                run.assert_not_called()
                self.assertIn('No provider checked', output.getvalue())
            Path(root, 'settings.json').write_text(json.dumps({'defaultProvider': 'openai', 'unrelated': 'private'}))
            with patch.object(admin.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
                with contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit):
                    admin.pi_status()
                self.assertNotIn('private', output.getvalue())
                self.assertEqual(run.call_args.args[0][4], 'openai')

    def test_terminal_status_only_queries_version(self):
        with patch.object(terminal.shutil, 'which', side_effect=lambda name: '/bin/pi' if name == 'pi' else None), \
                patch.object(terminal.subprocess, 'run', return_value=SimpleNamespace(stdout='1.6.0\n')) as run:
            result = terminal.status()
        self.assertEqual(result['items'][-1], {'id': 'pi', 'installed': True, 'version': '1.6.0'})
        run.assert_called_once_with(['/bin/pi', '--version'], capture_output=True, text=True, timeout=5)
        self.assertTrue({'pi-login', 'pi-status', 'pi-install', 'pi-update'} <= terminal.ACTIONS)


if __name__ == '__main__':
    unittest.main()
