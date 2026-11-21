"""Reviewed, credential-free instructions served to authenticated clients."""

SCRIPT = r'''#!/bin/sh
# EasyRead remote research workspace guide. Review before running.
# No software is installed and no credentials are read by this script.
set -eu
if [ "${1:-}" = "--check" ]; then
    if [ "$#" -lt 2 ] || [ "$#" -gt 4 ]; then
        echo "Usage: sh easyread-remote-setup.sh --check /absolute/workspace [codex|claude|pi] [/absolute/venv/bin/python]" >&2
        exit 2
    fi
    exec "${4:-python3}" -m easyread.workspaces.remote_worker --check --workspace "$2" --engine "${3:-codex}"
fi
if [ "$#" -ne 0 ]; then
    echo "Use no arguments for instructions, or --check /absolute/workspace [engine] [python]." >&2
    exit 2
fi
cat <<'GUIDE'
EasyRead SSH research workspaces — manual bootstrap

Run these steps yourself on the REMOTE host, using the intended SSH account.
This guide does not install packages, change your shell or copy credentials.

1. Install Git, Python 3.10+ with venv/pip, and a TeX distribution with latexmk
   and pdflatex. For example, review and run on Debian/Ubuntu:
     sudo apt-get update
     sudo apt-get install git python3 python3-venv latexmk texlive-latex-base
   For XeLaTeX/LuaLaTeX projects also install the corresponding TeX packages.

2. From the SAME reviewed checkout, explicitly install the pinned official
   Beads 1.3.4 and Dolt 2.7.5 releases with checksum verification:
     python3 scripts/install_workspace_deps.py --install --prefix "$HOME/.local"
   The Beads panel supports bd 1.3.1–1.3.4, not arbitrary latest releases.
   Official sources: https://github.com/gastownhall/beads and
     https://docs.dolthub.com/introduction/installation
   Put their executables on the noninteractive SSH PATH. Verify:
     git --version; bd --version; dolt version; latexmk -v; pdflatex --version
   Configure Git identity yourself if needed; EasyRead never invents identity.

3. Install your selected agent CLI using its official instructions:
     Codex: https://developers.openai.com/codex/cli
     Claude Code: https://code.claude.com/docs/en/setup
     Pi: https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent
   Authenticate on the REMOTE host with that CLI. EasyRead does not transfer
   your local provider keys, deployment environment, SSH agent or CLI auth.

4. Copy/check out the EXACT EasyRead source used by your local server. Worker
   protocol AND workspace source digest must match; a version tag is not enough.
   Review and substitute your own paths, then run on the REMOTE host:
     cd /absolute/path/to/easyread
     python3 -m venv .venv
     .venv/bin/python -m pip install -e .
     mkdir -p /absolute/path/to/research
     chmod 700 /absolute/path/to/research
     .venv/bin/python -m easyread.workspaces.remote_worker --check \
       --workspace /absolute/path/to/research --engine codex
   The workspace must exist, be writable and contain no symlink ancestors.
   Each project uses <workspace>/workspace/<id>; private state lives in
   <workspace>/.research-workspaces/<id>. Do not run two EasyRead clients against
   one workspace concurrently. For embedded native terminals install ttyd on
   the remote host, and set EASYREAD_TERMINAL=1 in both the deployment backend
   and the remote worker environment. Local ttyd is not required for remote TUI.
   You can create a fixed Python wrapper exporting EASYREAD_TERMINAL=1 before
   exec /absolute/venv/bin/python "$@", then configure that wrapper as Python.

5. On the LOCAL EasyRead server, install OpenSSH. Independently verify the host
   fingerprint with the administrator, then establish the known_hosts entry.
   Never blindly accept ssh-keyscan output. Test key login without passwords:
     ssh -o StrictHostKeyChecking=yes -o BatchMode=yes \
       -o ForwardAgent=no your-host-alias true
   EasyRead reads the deployment SERVICE ACCOUNT's trusted ~/.ssh/config,
   including Host aliases, HostName, User, Port, IdentityFile and ProxyJump.
   Leave user/port/key blank in the form to inherit configuration/defaults.
   Filled user/port values override config; an explicit key is prioritized with
   IdentitiesOnly=yes but OpenSSH may also try config IdentityFile entries.
   Local ssh-agent authentication is allowed; agent/X11 forwarding is disabled.
   Configure BatchMode yes and StrictHostKeyChecking yes on jump-host entries
   too: target options do not propagate to ProxyJump/ProxyCommand subprocesses.
   Config-owned proxy commands are trusted; the browser cannot supply SSH flags.
   EasyRead disables inherited control sockets/persistence, RemoteCommand,
   backgrounding and stdin/TTY overrides to own each worker's lifecycle. Only
   SessionType/StdinNull/ForkAfterAuthentication may be ignored by older clients
   lacking those options; strict host-key/authentication options are not ignored.

6. Add the server in EasyRead, using the absolute .venv/bin/python above, not
   system python3 unless that interpreter has the matching EasyRead installed.
   Run Check for the selected agent. A successful SSH key login does NOT mean
   Python can import EasyRead or that Git/Beads/TeX/Agent tools are runnable.
   Diagnostics distinguish DNS/network, host keys, key authentication, Python,
   missing worker/dependencies, mismatched builds and a busy worker.
   Creation rechecks readiness and removes failed Git/Beads allocations.
   Software discovery does not prove provider login or
   model access: verify those interactively on the remote host first.

All file, Git, Beads, TeX and AI operations (including pasted images) then run
remotely. No local fallback is used. Disconnect refuses active tasks and pauses
reconnection until Connect. Idle connections close automatically after 5 minutes.
After a lost response inspect remote state before retrying mutations. Pending
creation references retain their fixed target/id for recovery; deleting a
confirmed-missing project removes only its local reference.

Library access uses the EXISTING full EasyRead API/MCP, not project-scoped tokens:
  - Enable EASYREAD_OPEN_API=1 / EASYREAD_MCP=1 on the EasyRead deployment.
  - Configure its reachable /api/v1 or /mcp URL in the remote native Agent CLI.
  - Use the existing full Access protection code as Authorization: Bearer CODE.
    This has all existing Open API permissions, including whole-library reads
    and imports. It is not the browser X-Token. No extra restriction is added.
  - Store the code in private native CLI configuration/environment, not project
    files or Git. Restart the session after changing host CLI configuration.
  - Use your own SSH port forwarding/VPN/network setup if the host is unreachable.
Linking only records IDs in .easyread-papers.json; it does not copy paper content.
Fresh API reads see updated text, notes and highlights without relinking.
Exports contain project files/history, not host SSH or native CLI credentials.
Legacy snapshots are retained as user data, not selected automatically.
Archive import creates a NEW LOCAL project, never a remote target.
Embedded terminal input/resize is standard ttyd over SSH. Hiding its iframe
retains it; closing/reloading the browser stops that terminal, unlike a persistent
VTE/tmux session. Explicit stop/logout releases only the corresponding session.
GUIDE
'''


def main():
    print(SCRIPT, end='')


if __name__ == '__main__':
    main()
