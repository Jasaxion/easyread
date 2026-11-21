"""Reader Settings → CLI and terminal probes installation, never provider login."""
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from easyread.server import terminal


class TerminalStatusTest(unittest.TestCase):
    def test_pi_installation_is_rechecked_without_restart_or_provider_call(self):
        installed = False
        def executable(name):
            return '/native/bin/pi' if installed and name == 'pi' else None
        with patch.object(terminal.shutil, 'which', side_effect=executable), \
                patch.object(terminal.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='1.6.0\n', stderr='')) as run:
            before = terminal.status()['items'][-1]
            self.assertEqual(before, {'id': 'pi', 'installed': False, 'version': ''})
            run.assert_not_called()
            installed = True
            after = terminal.status()['items'][-1]
            self.assertEqual(after, {'id': 'pi', 'installed': True, 'version': '1.6.0'})
            self.assertEqual(run.call_args.args[0], ['/native/bin/pi', '--version'])
            self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)
            self.assertEqual(run.call_args.kwargs['timeout'], 5)

    def test_version_failure_is_not_reported_as_missing_and_never_exposes_stderr(self):
        for result, code in [
            (SimpleNamespace(returncode=1, stdout='secret-in-error-output', stderr='Authorization: Bearer private'), 'command_failed'),
            (subprocess.TimeoutExpired(['pi', '--version'], 5), 'timeout'),
            (OSError('sensitive launch details'), 'launch_failed'),
        ]:
            with self.subTest(code=code), patch.object(terminal.shutil, 'which', return_value='/native/pi'):
                kwargs = {'side_effect': result} if isinstance(result, Exception) else {'return_value': result}
                with patch.object(terminal.subprocess, 'run', **kwargs):
                    item = terminal.status()['items'][-1]
                self.assertEqual(item, {'id': 'pi', 'installed': True, 'version': '', 'version_error': code})
                self.assertNotIn('private', str(item)); self.assertNotIn('sensitive', str(item))

    def test_empty_version_keeps_installed_fact_and_stderr_version_is_supported(self):
        with patch.object(terminal.shutil, 'which', return_value='/native/pi'):
            for stdout, stderr, expected in [('', '', ''), ('', '1.6.0\n', '1.6.0')]:
                with patch.object(terminal.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr)):
                    item = terminal.status()['items'][-1]
                    self.assertTrue(item['installed']); self.assertEqual(item['version'], expected)
                    self.assertNotIn('version_error', item)


if __name__ == '__main__':
    unittest.main()
