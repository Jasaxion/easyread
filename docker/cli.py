#!/usr/bin/env python3
"""Explicit administrative CLI actions; never expose these as an app API."""
import argparse
import os
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description='EasyRead CLI login/update helper')
    parser.add_argument('action', choices=['status', 'login', 'auth-status', 'update'])
    parser.add_argument('cli', nargs='?', choices=['claude', 'codex', 'all'], default='all')
    args = parser.parse_args()
    from easyread.app import proxy
    proxy.apply_saved()
    if args.action == 'status':
        print('HOME:', os.environ.get('HOME'))
        for command in ('claude', 'codex', 'chromium'):
            print(command + ':', shutil.which(command) or 'not installed', flush=True)
            if shutil.which(command):
                subprocess.run([command, '--version'], check=True)
        print('Installed does not mean logged in. Use easyread-cli auth-status claude/codex.')
        return
    if args.action in ('login', 'auth-status') and args.cli == 'all':
        parser.error('Choose claude or codex')
    if args.action == 'login':
        command = ['claude', 'auth', 'login'] if args.cli == 'claude' else ['codex', 'login', '--device-auth']
    elif args.action == 'auth-status':
        command = ['claude', 'auth', 'status'] if args.cli == 'claude' else ['codex', 'login', 'status']
    else:
        print('Update only when translation/AI/PDF jobs are idle.', flush=True)
        if args.cli in ('claude', 'all'):
            from pathlib import Path
            executable = shutil.which('claude')
            npm_install = executable and '@anthropic-ai/claude-code' in Path(executable).resolve().as_posix()
            command = ['npm', 'install', '--global', '@anthropic-ai/claude-code@latest'] if npm_install else ['claude', 'update']
            subprocess.run(command, check=True)
        if args.cli in ('codex', 'all'):
            subprocess.run(['npm', 'install', '--global', '@openai/codex@latest'], check=True)
        return
    os.execvp(command[0], command)


if __name__ == '__main__':
    main()
