#!/usr/bin/env python3
"""Explicit administrative CLI actions; never expose these as an app API."""
import argparse
import os
import shutil
import subprocess
from pathlib import Path


PI_PACKAGE = '@earendil-works/pi-coding-agent@latest'


def pi_status(provider=None):
    """Explicit, credential-redacted status; never print API keys or refresh OAuth."""
    import json
    source = Path(os.environ.get('PI_CODING_AGENT_DIR') or Path.home() / '.pi/agent') / 'settings.json'
    print('Pi settings:', source, flush=True)
    if not provider:
        try:
            if source.stat().st_size <= 1024 * 1024:
                settings = json.loads(source.read_text(encoding='utf-8-sig'))
                provider = settings.get('defaultProvider') if isinstance(settings, dict) else None
        except (OSError, ValueError):
            pass
    if not isinstance(provider, str) or not provider.strip():
        print('Select a provider with /login and /model in pi, or run:')
        print('  easyread-cli auth-status pi --provider <provider>')
        print('No provider checked; installation alone does not mean authentication is ready.')
        return
    result = subprocess.run(['pi', 'auth', 'check', '--provider', provider.strip(), '--json', '--no-refresh'], timeout=30)
    raise SystemExit(result.returncode)


def main():
    parser = argparse.ArgumentParser(description='EasyRead CLI login/update helper')
    parser.add_argument('action', choices=['status', 'login', 'auth-status', 'install', 'update'])
    parser.add_argument('cli', nargs='?', choices=['claude', 'codex', 'pi', 'all'], default='all')
    parser.add_argument('--provider', help='Pi provider for auth-status (otherwise uses its native default)')
    args = parser.parse_args()
    if args.provider and (args.cli != 'pi' or args.action != 'auth-status'):
        parser.error('--provider requires auth-status pi')
    if args.action == 'install' and args.cli != 'pi':
        parser.error('install currently supports pi; use update for existing Claude/Codex installations')
    from easyread.app import proxy
    proxy.apply_saved()
    if args.action == 'status':
        print('HOME:', os.environ.get('HOME'))
        for command in ('claude', 'codex', 'pi', 'chromium'):
            print(command + ':', shutil.which(command) or 'not installed', flush=True)
            if shutil.which(command):
                subprocess.run([command, '--version'], check=True)
        print('Installed does not mean logged in. Use easyread-cli auth-status claude/codex/pi.')
        return
    if args.action in ('login', 'auth-status') and args.cli == 'all':
        parser.error('Choose claude, codex or pi')
    if args.cli == 'pi' and args.action == 'auth-status':
        pi_status(args.provider)
        return
    if args.cli == 'pi' and args.action == 'login':
        print('In Pi, run /login to connect a provider, then /model to choose a model. Exit with Ctrl+C twice.', flush=True)
        os.execvp('pi', ['pi', '--no-session'])
        return
    if args.action == 'login':
        command = ['claude', 'auth', 'login'] if args.cli == 'claude' else ['codex', 'login', '--device-auth']
    elif args.action == 'auth-status':
        command = ['claude', 'auth', 'status'] if args.cli == 'claude' else ['codex', 'login', 'status']
    else:
        print('Update only when translation/AI/PDF jobs are idle.', flush=True)
        if args.cli in ('claude', 'all'):
            executable = shutil.which('claude')
            npm_install = executable and '@anthropic-ai/claude-code' in Path(executable).resolve().as_posix()
            command = ['npm', 'install', '--global', '@anthropic-ai/claude-code@latest'] if npm_install else ['claude', 'update']
            subprocess.run(command, check=True)
        if args.cli in ('codex', 'all'):
            subprocess.run(['npm', 'install', '--global', '@openai/codex@latest'], check=True)
        if args.cli in ('pi', 'all'):
            subprocess.run(['npm', 'install', '--global', '--ignore-scripts', PI_PACKAGE], check=True)
            subprocess.run(['pi', '--version'], check=True)
        return
    os.execvp(command[0], command)


if __name__ == '__main__':
    main()
