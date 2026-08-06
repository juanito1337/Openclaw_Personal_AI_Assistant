# syntax=docker/dockerfile:1.7

# M7 supply-chain inputs. Human-readable tags document upstream versions while
# the digest is the immutable build input. Keep these values synchronized with
# docker/supply-chain.lock.json and tests/test_m7_supply_chain.py.
ARG OPENCLAW_BASE_IMAGE=ghcr.io/openclaw/openclaw:2026.7.1-2@sha256:8789721d2e9b24b780a1504b56deb4c6bd5c7dbf96a1dd117e7c45c2ed72c8ac
ARG NODE_BASE_IMAGE=node:24-alpine3.22@sha256:191c9f0080fcbbc6547a85dc0ff7988072214a355aabdc1d2ec55a7dae5eea8a
ARG PYTHON_BASE_IMAGE=python:3.11-alpine3.22@sha256:a4fc589b32e824f3f02ed9d7e7be19518aa47e105b80416336af9f202275a489
ARG HIMALAYA_VERSION=1.2.0
ARG HIMALAYA_ARCHIVE_SHA256=e04e6382e3e664ef34b01afa1a2216113194a2975d2859727647b22d9b36d4e4
ARG HIMALAYA_SHA256=9529d2584add1c4343f32524e6f985e7c98d491f3b854747318020eb1ec1df7f
ARG OPENCLAW_SOURCE_REVISION=local
ARG OPENCLAW_BUILD_CREATED=1970-01-01T00:00:00Z
ARG SOURCE_DATE_EPOCH=0
ARG OPENCLAW_VERSION=3.4.0-r27.2.5
ARG OPENCLAW_SOURCE_URL=https://github.com/juanito1337/Openclaw_Personal_AI_Assistant

FROM scratch AS agent-source
COPY VERSION RELEASE.json AGENTS.md HEARTBEAT.md README.md CHANGELOG.md /
COPY mail_agent /mail_agent
COPY personal_assistant /personal_assistant
COPY skills/personal-assistant /skills/personal-assistant
COPY scripts/assistant.sh scripts/mail-agent.sh scripts/ollama-priority-proxy.sh /scripts/
COPY docker/entrypoint.sh docker/healthcheck.sh docker/job_loop.py docker/clamav-update.sh /docker/

FROM ${OPENCLAW_BASE_IMAGE} AS openclaw-source

FROM ${NODE_BASE_IMAGE} AS himalaya-builder
ARG HIMALAYA_VERSION
ARG HIMALAYA_ARCHIVE_SHA256
ARG HIMALAYA_SHA256
SHELL ["/bin/ash", "-o", "pipefail", "-c"]
ADD --checksum=sha256:e04e6382e3e664ef34b01afa1a2216113194a2975d2859727647b22d9b36d4e4 \
    https://github.com/pimalaya/himalaya/releases/download/v1.2.0/himalaya.x86_64-linux.tgz \
    /tmp/himalaya.tgz
RUN test "$HIMALAYA_ARCHIVE_SHA256" = "e04e6382e3e664ef34b01afa1a2216113194a2975d2859727647b22d9b36d4e4" \
    && mkdir -p /opt/himalaya/bin \
    && tar --extract --gzip --file /tmp/himalaya.tgz --directory /opt/himalaya/bin himalaya \
    && rm /tmp/himalaya.tgz \
    && chmod 0555 /opt/himalaya/bin/himalaya \
    && touch --date=@0 /opt/himalaya/bin/himalaya \
    && actual=$(sha256sum /opt/himalaya/bin/himalaya | cut -d' ' -f1) \
    && printf 'himalaya-sha256=%s\n' "$actual" \
    && test "$actual" = "$HIMALAYA_SHA256" \
    && test "$(/opt/himalaya/bin/himalaya --version | sed -n '1s/^himalaya v\([^ ]*\).*/\1/p')" = "${HIMALAYA_VERSION}"

FROM ${NODE_BASE_IMAGE} AS runtime
ARG OPENCLAW_SOURCE_REVISION
ARG OPENCLAW_BUILD_CREATED
ARG OPENCLAW_VERSION
ARG OPENCLAW_SOURCE_URL
ARG HIMALAYA_SHA256
LABEL org.opencontainers.image.title="OpenClaw Personal Assistant runtime" \
      org.opencontainers.image.description="Gateway and workers with verified mail, OCR and antivirus dependencies" \
      org.opencontainers.image.version="${OPENCLAW_VERSION}" \
      org.opencontainers.image.created="${OPENCLAW_BUILD_CREATED}" \
      org.opencontainers.image.source="${OPENCLAW_SOURCE_URL}" \
      org.opencontainers.image.revision="${OPENCLAW_SOURCE_REVISION}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.base.name="node:24-alpine3.22" \
      org.opencontainers.image.base.digest="sha256:191c9f0080fcbbc6547a85dc0ff7988072214a355aabdc1d2ec55a7dae5eea8a" \
      org.opencontainers.image.openclaw.role="runtime" \
      org.opencontainers.image.openclaw.layout-min="1" \
      org.opencontainers.image.openclaw.layout-max="3" \
      org.opencontainers.image.openclaw.layout-current="3"

USER 0
SHELL ["/bin/ash", "-o", "pipefail", "-c"]
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Every direct package and every external image is immutable. The upstream
# OpenClaw filesystem is used only as the verified source for /app; the runtime
# OS is the substantially smaller Alpine base measured for M7.
RUN apk add --no-cache \
       bash=5.2.37-r0 \
       ca-certificates=20260611-r0 \
       curl=8.14.1-r3 \
       jq=1.8.1-r0 \
       rsync=3.4.3-r0 \
       sqlite=3.49.2-r1 \
       zstd=1.5.7-r0 \
       procps-ng=4.0.4-r3 \
       tini=0.19.0-r3 \
       python3=3.12.13-r0 \
       poppler-utils=25.04.0-r0 \
       tesseract-ocr=5.5.0-r2 \
       tesseract-ocr-data-deu=5.5.0-r2 \
       tesseract-ocr-data-eng=5.5.0-r2 \
       tzdata=2026c-r0 \
       clamav=1.4.3-r0 \
       freshclam=1.4.3-r0

COPY --from=openclaw-source /app /app
RUN rm -rf /app/node_modules/@vitest/browser \
       /usr/local/share/corepack \
       /usr/local/lib/node_modules/npm/node_modules/tar \
    && cp -a /app/node_modules/tar /usr/local/lib/node_modules/npm/node_modules/tar \
    && ln -s /app/openclaw.mjs /usr/local/bin/openclaw \
    && test "$(node -p 'require("/usr/local/lib/node_modules/npm/node_modules/tar/package.json").version')" = "7.5.19" \
    && test "$(openclaw --version)" = "OpenClaw 2026.7.1"

COPY --from=himalaya-builder /opt/himalaya/bin/himalaya /usr/local/bin/himalaya
COPY --from=agent-source / /opt/openclaw-agent
COPY docker/supply-chain.lock.json /usr/share/openclaw/supply-chain.lock.json
WORKDIR /opt/openclaw-agent
RUN printf '%s\n' "${OPENCLAW_SOURCE_REVISION}" > /opt/openclaw-agent/SOURCE_REVISION \
    && chmod 0444 /opt/openclaw-agent/SOURCE_REVISION \
    && chmod 0555 /opt/openclaw-agent/scripts/*.sh /opt/openclaw-agent/docker/*.sh \
    && chmod -R a-w /opt/openclaw-agent \
    && mkdir -p /home/node/.openclaw/workspace /home/node/.config/himalaya /var/lib/clamav \
    && chown -R node:node /home/node/.openclaw /home/node/.config \
    && chown -R clamav:clamav /var/lib/clamav \
    && ln -sf /opt/openclaw-agent/scripts/assistant.sh /usr/local/bin/personal-assistant \
    && ln -sf /opt/openclaw-agent/scripts/mail-agent.sh /usr/local/bin/mail-agent \
    && test "$(sha256sum /usr/local/bin/himalaya | cut -d' ' -f1)" = "${HIMALAYA_SHA256}"

ENV HOME=/home/node \
    OPENCLAW_RUNTIME=container \
    OPENCLAW_IMAGE_ROOT=/opt/openclaw-agent \
    OPENCLAW_CODE_ROOT=/opt/openclaw-agent \
    OPENCLAW_RELEASE_ROOT=/opt/openclaw-agent \
    OPENCLAW_STATE_ROOT=/home/node/.openclaw \
    OPENCLAW_WORKSPACE=/home/node/.openclaw/workspace \
    OPENCLAW_IMAGE_REVISION=${OPENCLAW_SOURCE_REVISION} \
    MAIL_AGENT_CONFIG=/home/node/.openclaw/workspace/mail_agent/config.toml \
    PERSONAL_ASSISTANT_CONFIG=/home/node/.openclaw/workspace/personal_assistant/config.toml \
    OPENCLAW_TOOLS_CONFIG=/home/node/.openclaw/workspace/personal_assistant/tools.toml \
    OPENCLAW_TOOL_DEFAULTS_CONFIG=/opt/openclaw-agent/personal_assistant/tool_defaults.toml \
    OPENCLAW_POLICY_DEFAULTS_CONFIG=/opt/openclaw-agent/personal_assistant/policy_defaults.toml \
    OPENCLAW_LOG_DIR=/home/node/.openclaw/workspace/personal_assistant/data/container_logs \
    OPENCLAW_JOB_STATUS_DIR=/home/node/.openclaw/workspace/personal_assistant/data/container_jobs \
    OLLAMA_PRIORITY_ENV_FILE=/etc/openclaw-agent/ollama-priority.env \
    PYTHONPATH=/opt/openclaw-agent \
    PYTHONSAFEPATH=1 \
    PATH=/opt/openclaw-agent/scripts:/usr/local/bin:/usr/bin:/bin

USER 1000:1000
ENTRYPOINT ["/sbin/tini", "--", "/opt/openclaw-agent/docker/entrypoint.sh"]
CMD ["openclaw", "gateway", "--bind", "lan", "--port", "18789"]

FROM ${PYTHON_BASE_IMAGE} AS proxy-runtime
ARG OPENCLAW_SOURCE_REVISION
ARG OPENCLAW_BUILD_CREATED
ARG OPENCLAW_VERSION
ARG OPENCLAW_SOURCE_URL
LABEL org.opencontainers.image.title="OpenClaw Ollama priority proxy" \
      org.opencontainers.image.description="Minimal Python runtime for the Ollama coordination proxy" \
      org.opencontainers.image.version="${OPENCLAW_VERSION}" \
      org.opencontainers.image.created="${OPENCLAW_BUILD_CREATED}" \
      org.opencontainers.image.source="${OPENCLAW_SOURCE_URL}" \
      org.opencontainers.image.revision="${OPENCLAW_SOURCE_REVISION}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.base.name="python:3.11-alpine3.22" \
      org.opencontainers.image.base.digest="sha256:a4fc589b32e824f3f02ed9d7e7be19518aa47e105b80416336af9f202275a489" \
      org.opencontainers.image.openclaw.role="proxy" \
      org.opencontainers.image.openclaw.layout-min="1" \
      org.opencontainers.image.openclaw.layout-max="3" \
      org.opencontainers.image.openclaw.layout-current="3"
USER 0
RUN apk add --no-cache \
       bash=5.2.37-r0 \
       ca-certificates=20260611-r0 \
       curl=8.14.1-r3 \
       tini=0.19.0-r3 \
    && addgroup -g 1000 -S node \
    && adduser -u 1000 -S -D -G node -h /home/node -s /sbin/nologin node
COPY VERSION RELEASE.json /opt/openclaw-agent/
COPY personal_assistant/__init__.py personal_assistant/container_entrypoint.py personal_assistant/ollama_priority_proxy.py /opt/openclaw-agent/personal_assistant/
COPY scripts/assistant.sh scripts/mail-agent.sh scripts/ollama-priority-proxy.sh /opt/openclaw-agent/scripts/
COPY docker/entrypoint.sh docker/healthcheck.sh /opt/openclaw-agent/docker/
COPY docker/supply-chain.lock.json /usr/share/openclaw/supply-chain.lock.json
WORKDIR /opt/openclaw-agent
RUN printf '%s\n' "${OPENCLAW_SOURCE_REVISION}" > SOURCE_REVISION \
    && chmod 0444 SOURCE_REVISION VERSION RELEASE.json \
    && chmod 0555 scripts/*.sh docker/*.sh \
    && chmod -R a-w /opt/openclaw-agent \
    && install -d -m 0700 -o node -g node /home/node/.openclaw/workspace
ENV HOME=/home/node \
    OPENCLAW_RUNTIME=container \
    OPENCLAW_IMAGE_ROOT=/opt/openclaw-agent \
    OPENCLAW_CODE_ROOT=/opt/openclaw-agent \
    OPENCLAW_RELEASE_ROOT=/opt/openclaw-agent \
    OPENCLAW_STATE_ROOT=/home/node/.openclaw \
    OPENCLAW_WORKSPACE=/home/node/.openclaw/workspace \
    OPENCLAW_IMAGE_REVISION=${OPENCLAW_SOURCE_REVISION} \
    PYTHONPATH=/opt/openclaw-agent \
    PYTHONSAFEPATH=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/openclaw-agent/scripts:/usr/local/bin:/usr/bin:/bin
USER 1000:1000
ENTRYPOINT ["/sbin/tini", "--", "/opt/openclaw-agent/docker/entrypoint.sh"]
CMD ["/opt/openclaw-agent/scripts/ollama-priority-proxy.sh", "serve"]

FROM ${PYTHON_BASE_IMAGE} AS maintenance-runtime
ARG OPENCLAW_SOURCE_REVISION
ARG OPENCLAW_BUILD_CREATED
ARG OPENCLAW_VERSION
ARG OPENCLAW_SOURCE_URL
LABEL org.opencontainers.image.title="OpenClaw ClamAV maintenance" \
      org.opencontainers.image.description="Minimal ClamAV signature updater and freshness verifier" \
      org.opencontainers.image.version="${OPENCLAW_VERSION}" \
      org.opencontainers.image.created="${OPENCLAW_BUILD_CREATED}" \
      org.opencontainers.image.source="${OPENCLAW_SOURCE_URL}" \
      org.opencontainers.image.revision="${OPENCLAW_SOURCE_REVISION}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.base.name="python:3.11-alpine3.22" \
      org.opencontainers.image.base.digest="sha256:a4fc589b32e824f3f02ed9d7e7be19518aa47e105b80416336af9f202275a489" \
      org.opencontainers.image.openclaw.role="maintenance" \
      org.opencontainers.image.openclaw.layout-min="1" \
      org.opencontainers.image.openclaw.layout-max="3" \
      org.opencontainers.image.openclaw.layout-current="3"
USER 0
RUN apk add --no-cache \
       bash=5.2.37-r0 \
       ca-certificates=20260611-r0 \
       clamav=1.4.3-r0 \
       freshclam=1.4.3-r0 \
       tini=0.19.0-r3
COPY VERSION RELEASE.json /opt/openclaw-agent/
COPY personal_assistant/__init__.py personal_assistant/clamav_health.py /opt/openclaw-agent/personal_assistant/
COPY docker/clamav-update.sh /opt/openclaw-agent/docker/
COPY docker/supply-chain.lock.json /usr/share/openclaw/supply-chain.lock.json
WORKDIR /opt/openclaw-agent
RUN printf '%s\n' "${OPENCLAW_SOURCE_REVISION}" > SOURCE_REVISION \
    && chmod 0444 SOURCE_REVISION VERSION RELEASE.json \
    && chmod 0555 docker/clamav-update.sh \
    && chmod -R a-w /opt/openclaw-agent \
    && install -d -m 0750 -o clamav -g clamav /var/lib/clamav /var/log/clamav
ENV OPENCLAW_RUNTIME=container \
    OPENCLAW_IMAGE_ROOT=/opt/openclaw-agent \
    OPENCLAW_RELEASE_ROOT=/opt/openclaw-agent \
    OPENCLAW_IMAGE_REVISION=${OPENCLAW_SOURCE_REVISION} \
    PYTHONPATH=/opt/openclaw-agent \
    PYTHONSAFEPATH=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER 100:101
ENTRYPOINT ["/sbin/tini", "--", "/opt/openclaw-agent/docker/clamav-update.sh"]

# Preserve the historical default: an unqualified `docker build .` produces the
# complete runtime, while Compose selects the measured smaller role targets.
FROM runtime AS final
