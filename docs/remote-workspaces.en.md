# SSH remote research workspaces

[Research workspaces](research-workspaces.en.md) · [Open API / MCP](open-api.md) · [简体中文](remote-workspaces.md)

Remote projects use the same EasyRead UI, with files, Git, Beads, LaTeX, model catalogs, Ask AI and embedded native terminals running on your own SSH server. Failures never fall back to deployment-host agents. Reader chat, translation and library-wide Q&A retain their existing behavior.

## Standard OS and SSH setup

The backend, including a Docker container, is an ordinary operating-system environment. Connections use **OpenSSH**, and terminals reuse **ttyd + PTY + WebSocket**. No dinotty installation, terminal sidecar or alternative SSH library is required.

1. Open **SSH servers** and download the environment script. It prints instructions by default; review and perform installation yourself. `--check` diagnoses readiness. You may use your own local/remote terminals for installation, login and connectivity troubleshooting.
2. Prepare Python 3.10+, Git, supported official Beads (`bd`), Dolt, `latexmk`, `pdflatex` and your chosen Codex, Claude Code or Pi. Install other TeX engines/packages and BibTeX/Biber as needed. The existing `scripts/install_workspace_deps.py --install` installs checksum-verified bd/Dolt releases.
3. Install the **same EasyRead source** remotely, preferably in a venv. Both protocol and source fingerprint must match; a matching version number is insufficient. Configure remote Agent login, Git identity and networking yourself.
4. Create a dedicated writable absolute workspace without symlink ancestors. Files live under `<configured directory>/workspace/<project-id>`, private state under `<configured directory>/.research-workspaces/<project-id>`. Do not let two EasyRead services own the same remote workspace.
5. On the **deployment host/container**, configure dedicated keys and verified `known_hosts` as the EasyRead service account. The form accepts a local private-key path, not passwords or key contents; a local ssh-agent may also authenticate. Host-key verification is enforced and agent forwarding is disabled.
6. Enter a host/IP or **Host alias from the deployment service account's `~/.ssh/config`**, workspace and remote Python executable. OpenSSH honors `HostName`, `User`, `Port`, `IdentityFile` and `ProxyJump`. Leave user, port and key fields blank to inherit SSH configuration/defaults. Filled user/port fields override configuration; existing saved explicit fields stay explicit until cleared. An explicit key is prioritized with `IdentitiesOnly=yes`, but OpenSSH's additive `IdentityFile` rules still apply. Use the **absolute venv Python path** from step 3, not system `python3` unless it has the matching EasyRead installation.
7. Validate the selected Agent, then create the project. Creation checks readiness again and requires successful Git/Beads initialization. A green check does not prove provider credits, login or every package your paper needs.

```bash
/path/to/venv/bin/python -m easyread.workspaces.remote_worker --check \
  --workspace /home/research/easyread-workspaces --engine codex
```

### SSH configuration and diagnostics

The trusted configuration belongs to the account running EasyRead, **not the browser user**. In Docker, place config, keys and verified `known_hosts` in that service account's SSH directory. Normal OpenSSH ownership/permissions apply. EasyRead does not parse SSH config or accept browser-supplied SSH flags.

Target connections enforce `BatchMode=yes`, `StrictHostKeyChecking=yes`, no agent/X11 forwarding and no automatic host-key updates. **These target options do not propagate to `ProxyJump`/`ProxyCommand` subprocesses**: configure `BatchMode yes` and `StrictHostKeyChecking yes` for jump hosts too, and verify their keys independently. Proxy commands in service-owned config are trusted. Inherited `ControlMaster`, `ControlPersist`, `ControlPath`, `RemoteCommand`, backgrounding, stdin and TTY/session overrides are disabled so a fresh EasyRead worker remains owned by its connection. Compatibility ignores only the newer option names `SessionType`, `StdinNull` and `ForkAfterAuthentication` on older clients that do not support them; host-key/authentication options are never ignored.

Check distinguishes connection, worker availability and executable readiness:

| Code / check | What to inspect |
| --- | --- |
| `remote_ssh_client` | OpenSSH on the deployment host/container |
| `remote_dns` | Alias/`HostName` in the service account config and DNS |
| `remote_network` | Port, firewall/VPN and jump/proxy reachability |
| `remote_host_key` | Independently verified fingerprints and `known_hosts`; never bypass checking |
| `remote_auth` | SSH user, authorized keys, selected identities or local agent; no password prompts |
| `remote_python` | Configured Python exists, is executable and is Python 3.10+ |
| `remote_worker_missing` / `remote_dependencies` | Install the matching EasyRead source and dependencies in **that interpreter**; select its absolute `venv/bin/python` path |
| `remote_protocol` | Exact same reviewed EasyRead workspace source on both sides, not only matching version tags |
| `remote_busy` | Another EasyRead worker owns the workspace; disconnect it first |
| `remote_start_timeout` / `remote_worker_failed` | Noninteractive SSH/Python startup or unsupported diagnostics; inspect privately using the setup guide |
| Tool checks | **Not found** means missing from the worker PATH; **found but version check failed** means installed but not executable/supported in this environment |

Startup diagnostics use bounded output and fixed messages, never raw SSH/proxy stderr or remote tracebacks. Unknown failures are not called authentication failures. Startup has a deadline and no automatic installation or operation retry. Reports include the configured Python and distinguish a running worker from missing tools. A working SSH login, worker, or Agent version check does **not** prove provider login, model access or quota. Optional ttyd absence does not block nonterminal project work.

## Embedded remote terminal

- Set `EASYREAD_TERMINAL=1` on the deployment backend. Full Compose enables it by default; lite may enable it explicitly. **Local ttyd is not required for a remote terminal.**
- Install ttyd remotely and set `EASYREAD_TERMINAL=1` in the noninteractive remote worker environment. The switches are independently controlled; local provider credentials/environment are not automatically forwarded.
- If SSH does not load the desired shell configuration, create your own executable wrapper and use its absolute path as the remote Python command:

```sh
#!/bin/sh
export EASYREAD_TERMINAL=1
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
exec /absolute/path/to/venv/bin/python "$@"
```

Choose a project Agent session and click **Show native terminal** to use the same native session in its TUI. ttyd/PTY handles keyboard/control input and resize; the browser connects only to an EasyRead same-origin URL. No public remote ttyd port is needed.

**Return to GUI** hides the iframe but retains terminal ownership, so GUI sends cannot race it. **Stop and close terminal** ends that process, releases ownership and preserves history. Browser refresh/closure or SSH loss follows the existing ttyd lifecycle and stops the terminal process. This is not persistent Dinotty VTE/tmux: stored session identity/history does not mean the same PTY survives. WebSocket attachment never silently creates/restarts a session. Logout closes that access session's terminals without touching others.

## Full-library API / MCP with the existing complete access code

Linking stores IDs in the remote project's `.easyread-papers.json`, **not content snapshots or project-specific library permissions**. Configure the remote native CLI with the existing EasyRead API/MCP to read the entire library, including unlinked papers and current notes/highlights, without relinking.

**Library API setup** provides configuration examples but does not read or store real tokens. Enable the existing endpoints on EasyRead, following [Open API](open-api.md):

```yaml
# Compose environment (or corresponding .env variables)
EASYREAD_OPEN_API: "1"
EASYREAD_MCP: "1"
EASYREAD_PUBLIC_URL: "https://your-easyread.example.com"
```

Restart the backend, then configure the URL and full credential in the **remote account's private native CLI configuration/environment**. `Authorization: Bearer <existing Settings → Access protection code>` is the existing complete API credential. There is no additional project-scoped token. It retains all existing Open API permissions, including library-wide reads and imports. Browser `X-Token` is a separate CSRF mechanism. The existing API surface is unchanged; this does not add system-settings or terminal-management endpoints to it.

```bash
export EASYREAD_BASE='https://your-easyread.example.com'
# Set EASYREAD_CODE securely on the remote host, not in project files or Git.
curl --fail -H "Authorization: Bearer $EASYREAD_CODE" "$EASYREAD_BASE/api/v1/info"
curl --fail -H "Authorization: Bearer $EASYREAD_CODE" "$EASYREAD_BASE/api/v1/documents?limit=10"
codex mcp add easyread --url "$EASYREAD_BASE/mcp" --bearer-token-env-var EASYREAD_CODE
```

Claude Code can use the [standard HTTP MCP JSON configuration](open-api.md#mcp-连接). Pi uses the user's installed MCP extension or API skill. Configure the remote account's native CLI HOME and reopen the session. EasyRead preserves that configuration and no longer automatically injects a restricted snapshot MCP into remote Codex/Claude. Missing setup is explained rather than substituting old snapshots for live content.

The URL must be reachable from the remote host. Its `localhost` refers to itself; use the deployment URL, VPN or user-managed standard SSH port forwarding. Use HTTPS for network access and retain existing Host/access protection. Legacy snapshot files are preserved as user data but are not the default library source.

## Docker and data boundaries

Full and lite backends include OpenSSH. Full also has local ttyd; remote execution uses remote ttyd. Compose exposes API/MCP/terminal switches without changing access protection automatically. Keep dedicated keys and `known_hosts` available to the container service account, for example in its CLI HOME volume; mounting an entire personal HOME is unnecessary.

This is a trusted personal workspace, not an OS sandbox. The remote account/Agent may hold the complete EasyRead API credential as requested by its owner. EasyRead does not put those native CLI credentials into research files or exports, but secrets written there by the user/Agent can still be exported.

Local `<library>/.remote-workspaces/registry.json` holds connection settings and offline project references. Files and native histories remain remote; back up both sides. Exports come from the remote project; imports create a new local project and never accept an archive-selected remote execution target.

## Lifecycle and verification

SSH is reused on demand. Idle reclamation checks Agent, goal, compilation and terminal ownership. Explicit disconnect requires stopping active work and pauses automatic reconnect until **Reconnect**. Hidden pages pause main polling, failures back off, and input/writes are not replayed automatically. Inspect remote state before retrying an ambiguous operation.

```bash
python -m unittest discover -s tests -p 'test_workspace_remote*.py' -v
node --test tests/web/test_workspace_servers.cjs tests/web/test_workspace_library_access.cjs
# Linux; temporary sshd/keys/directories, never a user's server:
EASYREAD_TEST_SSH=1 python -m unittest tests.test_workspace_ssh_integration -v
```

Actual SSH/ttyd tests use an interactive fake Agent to check input, resize, authorization, GUI/TUI exclusion, cleanup and full API reads. They do not replace provider login or real-paper compilation acceptance.

Design reference: [dinotty](https://github.com/xichan96/dinotty), MIT, revision `e701cc6c0dedf83a4ff32f8ae4ea95889059c552`, for separate terminal creation/attachment, session ownership and streaming I/O. No Rust/Vue source was copied or bundled; EasyRead reuses its existing ttyd and OpenSSH components.
