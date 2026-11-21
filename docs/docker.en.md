# Docker: one service, one reading space

[简体中文](docker.md)

The full image includes **Claude Code, Codex CLI, Pi Agent, Chromium, BabelDOC, Git, Beads/Dolt, three TeX engines and MCP**. A single container and port serve the reader, access-code settings, CLI management and embedded Web Terminal. There are no gateway/terminal sidecars, secondary ports or browser HTTP Basic prompts.

## Start

Use Docker Engine 24+, Compose v2 and at least 4 GB RAM. Reserve at least 20 GB disk for the full research image including TeX (layer caches need extra space). The [research workspace guide](research-workspaces.en.md) covers the trusted private-deployment boundary, prerequisites, version controls and real compile checks.

```bash
git clone https://github.com/Jasaxion/easyread.git
cd easyread
cp .env.example .env
docker compose up -d --build
```

Open **http://localhost:8765**. Defaults publish only on loopback and require no username. The first build downloads CLIs, browser/PDF components, the layout model, Beads/Dolt and TeX, then runs real dependency/compiler smoke checks. Later starts can use `docker compose up -d`.

## Access code

Use **Settings → Access protection** to enable, disable or change the code:

- When enabled, visitors see a dedicated EasyRead access page. Enter one code; there is no username or HTTP Basic dialog.
- Codes require 6–256 characters; a long random string is recommended. Only a salted hash is stored, not retrievable plaintext.
- Changing or disabling protection requires the current code. Other browser sessions are revoked and all terminals close; the current browser stays authorized.
- `EASYREAD_ACCESS_CODE` optionally seeds the code on first startup. Later settings take precedence; changing the environment does not overwrite the saved code.
- Sessions use HttpOnly/SameSite cookies and expire on server restart.

Access protection defaults to off for local personal use. **Configure a code before exposing the service to a network. With protection disabled, anyone who can reach the app can read/write papers, invoke models and use the container terminal.**

```dotenv
EASYREAD_BIND_IP=0.0.0.0
EASYREAD_PORT=8765
EASYREAD_ALLOWED_HOSTS=192.168.1.20,easyread.lan
EASYREAD_ACCESS_CODE=replace-with-your-own-long-random-code
```

The allowlist contains server IPs/domains, not client IPs, and is not authentication. A server administrator who forgets the code can stop the container, remove only `/data/EasyRead/.access.json`, set a new `EASYREAD_ACCESS_CODE`, and restart. Do not remove the library.

### Remote access

For SSH access without public exposure:

```bash
ssh -N -L 8765:127.0.0.1:8765 user@your-server
```

Then open localhost. Use an external HTTPS reverse proxy for public/untrusted networks. Preserve Host, Origin, Cookie and WebSocket Upgrade/Connection, disable streaming-response buffering and allow required uploads (up to 200 MiB). Deploy at the domain root and forward `/terminal/` to the same backend. Automatic self-signed certificates are no longer used; local HTTP avoids certificate warnings but does not encrypt transport.

## CLI and terminal inside Settings

Open **Settings → CLI & terminal**:

- Claude Code / Codex / Pi Agent cards show installed versions. Missing Pi installations can be installed through npm.
- After installation/update, click **Rescan** without closing the terminal, or wait for the roughly 15-second visible-panel refresh. Returning to the page also refreshes status. Only CLI cards update; the terminal iframe is not recreated. Polling pauses outside this settings tab and in hidden pages. This checks the EasyRead deployment host/container, not an SSH research workspace.
- A failed/timed-out version query is distinguished from a missing executable. Failed status requests retain the previous result. An installed version does not prove provider login.
- **Sign in**, **Login status** and **Update** run directly in the embedded terminal.
- **Open terminal** starts an interactive shell; no second website or password.
- Open CLI login links in your own browser and paste verification codes when requested. Codex uses device authentication rather than a publicly exposed OAuth callback.
- **Disconnect**, switching tabs or closing Settings ends the terminal and unfinished commands. Complete login/update operations first.

Available terminal commands:

```bash
easyread-cli status
easyread-cli login claude
easyread-cli login codex
easyread-cli auth-status claude
easyread-cli auth-status codex
easyread-cli install pi
easyread-cli login pi             # Use /login and /model inside Pi
easyread-cli auth-status pi       # Native default provider, or add --provider anthropic
easyread-cli update all
```

Pi installation/updates use `npm install --global --ignore-scripts @earendil-works/pi-coding-agent@latest` (Node.js 22.19+). Builds default to `PI_AGENT_VERSION=latest`; set an exact version in `.env` if needed. Docker cache does not track npm latest changes: use the terminal updater for a new release. Existing CLI HOME volumes are never overwritten by rebuilt images; click **Install** if an older HOME has no Pi. Pi authentication status checks only the chosen provider, without refreshing credentials or printing keys; an unset default shows setup instructions. Pi full mode has no built-in sandbox or per-tool approval prompts; use trusted private deployments only.

Installation does not prove login, account entitlement, regional availability or quotas. Host logins are not copied and paid model calls are not made automatically. Update CLIs while model/PDF jobs are idle.

ttyd is reused internally, launched on demand on container loopback only and reached through the main site's access/origin checks. This is a personal administrator shell, not a multi-user sandbox. Do not share library write access with untrusted people.

## Optional global proxy

**Settings → Network & proxy** controls `http_proxy`, `https_proxy` and `no_proxy`:

```text
http_proxy  = http://172.16.1.116:7890
https_proxy = http://172.16.1.116:7890
```

The IP above is only a local test example. HTTPS targets use HTTP CONNECT, so both fields can use `http://`. TLS-to-proxy `https://` URLs and SOCKS are not currently supported.

Settings persist in the app-data volume and affect new API/CLI/import requests. Reopen existing terminals after changing them. Disabling proxies clears the app's proxy variables; unset settings inherit the process environment. Internal connections bypass external proxies. Host settings and other containers are unchanged.

**Test proxy connection** tests network reachability to the Claude download service and OpenAI, not model accounts or quotas. Proxy credentials are redacted; Chromium dynamic imports do not support proxy URLs containing credentials.

Before building/starting, `.env` can supply defaults:

```dotenv
EASYREAD_BUILD_PROXY=http://172.16.1.116:7890
EASYREAD_HTTP_PROXY=http://172.16.1.116:7890
EASYREAD_HTTPS_PROXY=http://172.16.1.116:7890
```

Build proxies do not configure the Docker daemon's base-image pulls. Use `host.docker.internal` for a host proxy/Ollama; container loopback is not the host.

For slow Python dependency downloads, both image targets accept an optional mirror:

```dotenv
EASYREAD_PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
EASYREAD_BUILD_NO_PROXY=localhost,127.0.0.1,.aliyun.com,.tsinghua.edu.cn
```

`https://pypi.tuna.tsinghua.edu.cn/simple` is another option. Mirror traffic is direct while overseas CLI downloads keep using the build proxy. This affects builds only, does not change model API endpoints, and does not disable TLS verification.

## Research workspace and Obsidian

Both full and lite images include the research workspace, reading paths, editable mind maps, annotations, cross-paper analysis and Markdown export ported from PaperRead. Open `/research` or choose **Research Explorer** in the library. Research uses the same access-code protection. AI actions still require an authenticated CLI or a configured API model.

Semantic retrieval downloads a pinned multilingual model on first use (about 250 MB), cached under `/data/EasyRead/.research-os/` in the app-data volume. If downloading fails, configure the proxy and retry. Keyword retrieval remains available while the model is prepared.

An Obsidian Vault must be a **writable container path**. Use `/data/EasyRead/vault` to keep exports in the existing data volume without a host bind mount. To share a host Vault, create `docker-compose.override.yml`:

```yaml
services:
  easyread:
    volumes:
      - ./vault:/vault
```

Create the host `./vault` directory and give container UID/GID 1000 read/write permission, then run `docker compose up -d`. Enter `/vault` in research settings or the first export prompt, not a path on the browser computer. Headless containers offer a manual path prompt. Do not use the read-only `:ro` import mount for a Vault.

Re-export preserves user text outside managed blocks. Mind-map exports retain editable Mermaid source alongside the PNG preview. See the [research guide](research.en.md).

## Code-only updates with an existing full runtime

When a working `easyread:local` full image exists and Python dependencies have not changed, reuse it instead of downloading the CLI/browser/PDF toolchain again:

```bash
docker build -f docker/Dockerfile.app --build-arg BASE_IMAGE=easyread:local \
  --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple -t easyread:next .
docker tag easyread:next easyread:local
docker compose up -d --no-build
```

This reinstalls the application and runs `pip check`. Use the root Dockerfile for first installation or runtime/dependency changes.

## Data and updates

| Named volume (prefixed by Compose project) | Content |
| --- | --- |
| `easyread-data` | Papers, notes, progress, API/proxy settings and access-code hash |
| `easyread-cli-home` | CLI installations/updates, logins, configuration and PDF caches |

Mounts are `/data/EasyRead` and `/home/easyread`, owned by non-root UID/GID 1000. Recreation preserves existing CLI HOME. User-updated CLI versions are not overwritten by app-image upgrades.

```bash
git pull
docker compose up -d --build --remove-orphans
docker compose ps
docker compose logs -f --tail=100
```

When upgrading the older three-service deployment, `--remove-orphans` removes gateway/terminal containers. The old `easyread-auth` volume and username/password are no longer used. Existing paper and CLI HOME volumes remain shared with the new container.

Use `docker compose down` to stop/remove containers. **Do not use `down -v` for routine updates:** it deletes data. Keep the project name stable; never run simultaneous writers against the same library. Stop services when backing up both data directories and treat CLI credentials as sensitive.

## Boundaries

- Chromium keeps its sandbox using Moby's default seccomp policy plus clone/setns/unshare for user namespaces. No privileged mode, SYS_ADMIN, Docker socket or host HOME. The host must permit user namespaces.
- Isolated BabelDOC includes the layout model; public font assets may download on first PDF generation. A working translation model is still required.
- Folder/Zotero imports and linked PDFs use container paths, e.g. mount `./papers:/papers:ro` and enter `/papers`.
- Image generation depends on Codex/image API capabilities; separately billed image APIs are not invoked automatically.
- Electron OS integration and client-native folder dialogs are not included. [PWA and offline text/notes](guide.en.md#install-as-a-pwa-and-read-offline) require trusted HTTPS for mobile devices, deployed at the domain root. Proxy `/sw.js`, `/manifest.webmanifest`, `/pwa/` and `/web/` unchanged; never give `sw.js` a long cache lifetime.
- Optional lite API image: `docker compose -f docker-compose.lite.yml up -d --build`, without an embedded terminal. Do not run both against the same port/data volume.
