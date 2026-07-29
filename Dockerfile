# syntax=docker/dockerfile:1.7
ARG OPENCLAW_BASE_IMAGE=ghcr.io/openclaw/openclaw:2026.6.11
ARG HIMALAYA_VERSION=1.2.0

FROM rust:1.88-bookworm AS himalaya-builder
ARG HIMALAYA_VERSION
RUN cargo install himalaya --version "${HIMALAYA_VERSION}" --locked

FROM ${OPENCLAW_BASE_IMAGE}
ARG OPENCLAW_SOURCE_REVISION=local

USER root
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       bash ca-certificates curl jq rsync sqlite3 zstd procps tini \
       python3 python3-pip python3-venv \
       poppler-utils tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng \
       clamav clamav-freshclam \
    && rm -rf /var/lib/apt/lists/*

COPY --from=himalaya-builder /usr/local/cargo/bin/himalaya /usr/local/bin/himalaya
COPY . /opt/openclaw-agent
WORKDIR /opt/openclaw-agent
RUN printf '%s\n' "${OPENCLAW_SOURCE_REVISION}" > /opt/openclaw-agent/SOURCE_REVISION \
    && chmod 0444 /opt/openclaw-agent/SOURCE_REVISION \
    && python3 -m pip install --break-system-packages --no-cache-dir . \
    && chmod +x /opt/openclaw-agent/scripts/*.sh \
       /opt/openclaw-agent/docker/*.sh \
       /opt/openclaw-agent/docker/scripts/*.sh \
    && mkdir -p /home/node/.openclaw /home/node/.config/himalaya /var/lib/clamav \
    && chown -R node:node /home/node/.openclaw /home/node/.config

ENV HOME=/home/node \
    OPENCLAW_RUNTIME=container \
    OPENCLAW_WORKSPACE=/home/node/.openclaw/workspace \
    MAIL_AGENT_CONFIG=/home/node/.openclaw/workspace/mail_agent/config.toml \
    PERSONAL_ASSISTANT_CONFIG=/home/node/.openclaw/workspace/personal_assistant/config.toml \
    OPENCLAW_TOOLS_CONFIG=/home/node/.openclaw/workspace/personal_assistant/tools.toml \
    OPENCLAW_TOOL_DEFAULTS_CONFIG=/opt/openclaw-agent/personal_assistant/tool_defaults.toml \
    OPENCLAW_POLICY_DEFAULTS_CONFIG=/opt/openclaw-agent/personal_assistant/policy_defaults.toml \
    OPENCLAW_LOG_DIR=/home/node/.openclaw/workspace/personal_assistant/data/container_logs \
    OPENCLAW_JOB_STATUS_DIR=/home/node/.openclaw/workspace/personal_assistant/data/container_jobs \
    OLLAMA_PRIORITY_ENV_FILE=/etc/openclaw-agent/ollama-priority.env \
    PATH=/opt/openclaw-agent/scripts:/usr/local/bin:/usr/bin:/bin

USER node
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/openclaw-agent/docker/entrypoint.sh"]
CMD ["openclaw", "gateway", "--bind", "lan", "--port", "18789"]
