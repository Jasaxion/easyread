ARG NODE_IMAGE=node:22-bookworm-slim
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${NODE_IMAGE} AS node

FROM ${PYTHON_IMAGE} AS python-deps
WORKDIR /opt/easyread
# Semantic retrieval is available in both full and lite images.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
# Large wheels can pause behind an outbound proxy; keep retries bounded.
ARG PIP_DEFAULT_TIMEOUT=120
ARG PIP_RETRIES=5
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY pyproject.toml /tmp/easyread-pyproject.toml
RUN python -c "import subprocess,sys,tomllib; subprocess.check_call([sys.executable,'-m','pip','install','--no-cache-dir',*tomllib.load(open('/tmp/easyread-pyproject.toml','rb'))['project']['dependencies']])" \
    && useradd --create-home --uid 1000 easyread \
    && mkdir -p /data/EasyRead && chown -R easyread:easyread /data
ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    EASYREAD_HOME=/data/EasyRead \
    EASYREAD_HOST=0.0.0.0 \
    EASYREAD_PORT=8765
VOLUME ["/data/EasyRead"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('EASYREAD_PORT', '8765') + '/healthz', timeout=3).close()"

FROM python-deps AS lite
ARG PIP_DEFAULT_TIMEOUT=120
ARG PIP_RETRIES=5
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY pyproject.toml README.md LICENSE ./
COPY easyread ./easyread
RUN python -m pip install --no-cache-dir --no-deps .
USER easyread
CMD ["python", "-m", "easyread", "serve"]

# Heavy dependencies are independent of app source, so UI updates reuse them.
FROM python-deps AS full-deps
ARG BEADS_VERSION=1.3.4
ARG DOLT_VERSION=2.7.5
# Required for an unpinned Dolt version; use the release asset SHA-256.
ARG DOLT_SHA256=
ARG TEXLIVE_PACKAGES="texlive-latex-base texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended texlive-xetex texlive-luatex texlive-lang-chinese texlive-bibtex-extra latexmk biber"
ARG PIP_DEFAULT_TIMEOUT=120
ARG PIP_RETRIES=5
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
COPY scripts/install_workspace_deps.py /usr/local/bin/easyread-workspace-deps
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       bash curl git ripgrep chromium chromium-sandbox fonts-noto-cjk \
       libgomp1 zstd libzstd1 util-linux ${TEXLIVE_PACKAGES} \
    && rm -rf /var/lib/apt/lists/*
RUN python /usr/local/bin/easyread-workspace-deps --install --prefix /usr/local \
       --beads-version "$BEADS_VERSION" --dolt-version "$DOLT_VERSION" --dolt-sha256 "$DOLT_SHA256"
RUN case "$(dpkg --print-architecture)" in \
         amd64) ttyd_arch=x86_64; ttyd_sha=8a217c968aba172e0dbf3f34447218dc015bc4d5e59bf51db2f2cd12b7be4f55 ;; \
         arm64) ttyd_arch=aarch64; ttyd_sha=b38acadd89d1d396a0f5649aa52c539edbad07f4bc7348b27b4f4b7219dd4165 ;; \
         *) echo 'Unsupported ttyd architecture' >&2; exit 1 ;; \
       esac \
    && curl -fsSL --retry 3 "https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.$ttyd_arch" -o /tmp/ttyd \
    && echo "$ttyd_sha  /tmp/ttyd" | sha256sum -c - \
    && install -m 755 /tmp/ttyd /usr/local/bin/ttyd && rm /tmp/ttyd \
    && python -m venv --system-site-packages /opt/babeldoc \
    && /opt/babeldoc/bin/python -m pip install --no-cache-dir \
       'babeldoc==0.6.4' 'bitstring<5' 'onnxruntime==1.23.2'
ENV HOME=/home/easyread \
    NPM_CONFIG_PREFIX=/home/easyread/.npm-global \
    PATH=/home/easyread/.local/bin:/home/easyread/.npm-global/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \
    EASYREAD_BABELDOC_PYTHON=/opt/babeldoc/bin/python \
    DISABLE_AUTOUPDATER=1
ARG CLAUDE_CODE_VERSION=2.1.309
ARG CODEX_VERSION=0.169.0
ARG PI_AGENT_VERSION=latest
USER easyread
# The checker changes independently of the heavy dependency install layer.
COPY docker/check_workspace_runtime.py /usr/local/bin/easyread-check-workspace-runtime
# Real smoke checks use disposable HOME/projects, never runtime data/auth.
RUN python /usr/local/bin/easyread-check-workspace-runtime
# Official npm packages include the native platform binaries and integrity
# metadata; avoid downloading the latest installer binary before a pinned CLI.
RUN npm install --global "@anthropic-ai/claude-code@$CLAUDE_CODE_VERSION" "@openai/codex@$CODEX_VERSION" \
    && npm install --global --ignore-scripts "@earendil-works/pi-coding-agent@$PI_AGENT_VERSION" \
    && claude --version && codex --version && pi --version \
    && rm -rf /home/easyread/.npm /home/easyread/.claude/downloads
# Cache verified CLI installations independently of public model downloads.
RUN /opt/babeldoc/bin/python -c "from babeldoc.docvision.doclayout import DocLayoutModel; DocLayoutModel.load_onnx()"
USER root
RUN mv /home/easyread /opt/easyread-cli-seed \
    && mkdir /home/easyread && chown easyread:easyread /home/easyread

FROM full-deps AS full
ARG PIP_DEFAULT_TIMEOUT=120
ARG PIP_RETRIES=5
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY pyproject.toml README.md LICENSE ./
COPY easyread ./easyread
RUN python -m pip install --no-cache-dir --no-deps .
COPY docker/runtime.py /usr/local/bin/easyread-runtime
COPY docker/cli.py /usr/local/bin/easyread-cli
COPY docker/terminal-session.sh /usr/local/bin/easyread-terminal-session
RUN chmod 755 /usr/local/bin/easyread-runtime /usr/local/bin/easyread-cli /usr/local/bin/easyread-terminal-session
USER easyread
RUN python /usr/local/bin/easyread-check-workspace-runtime --adapter
VOLUME ["/home/easyread"]
ENTRYPOINT ["python", "/usr/local/bin/easyread-runtime"]
CMD ["python", "-m", "easyread", "serve"]
