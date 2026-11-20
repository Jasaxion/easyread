# Fast code-only update from an existing complete EasyRead runtime.
# First installation / changed runtime dependencies: use the root Dockerfile.
# Research workspaces require a full base rebuilt with bd/Dolt, TeX and MCP.
ARG BASE_IMAGE=easyread:local
FROM ${BASE_IMAGE} AS full
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_DEFAULT_TIMEOUT=60
ARG PIP_RETRIES=3
USER root
WORKDIR /opt/easyread
RUN rm -rf /opt/easyread/easyread
COPY pyproject.toml README.md LICENSE ./
COPY easyread ./easyread
RUN python -m pip install --no-cache-dir --no-deps --force-reinstall . \
    && python -m pip check
COPY scripts/install_workspace_deps.py /usr/local/bin/easyread-workspace-deps
COPY docker/check_workspace_runtime.py /usr/local/bin/easyread-check-workspace-runtime
# Fail clearly instead of labelling an old CLI-only base a complete runtime.
RUN python -c "import shutil,mcp; missing=[x for x in ('git','bd','dolt','pdflatex','xelatex','lualatex','latexmk') if not shutil.which(x)]; assert not missing, 'Rebuild root Dockerfile full target; missing: '+str(missing)"
COPY docker/runtime.py /usr/local/bin/easyread-runtime
COPY docker/cli.py /usr/local/bin/easyread-cli
COPY docker/terminal-session.sh /usr/local/bin/easyread-terminal-session
RUN chmod 755 /usr/local/bin/easyread-runtime /usr/local/bin/easyread-cli /usr/local/bin/easyread-terminal-session
USER easyread
RUN python /usr/local/bin/easyread-check-workspace-runtime --adapter
ENTRYPOINT ["python", "/usr/local/bin/easyread-runtime"]
CMD ["python", "-m", "easyread", "serve"]
