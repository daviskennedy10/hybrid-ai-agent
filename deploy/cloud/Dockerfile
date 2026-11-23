# Milestone resolver supplies immutable multi-platform image index digests.
ARG PYTHON_IMAGE
ARG TAILSCALE_IMAGE
FROM ${TAILSCALE_IMAGE} AS tailscale
FROM ${PYTHON_IMAGE} AS build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential git libffi-dev libssl-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY hermes-agent /app/hermes-agent
# Use the upstream frozen dependency graph, not the Mac virtualenv or a new resolver.
RUN pip install --no-cache-dir uv==0.11.7
WORKDIR /app/hermes-agent
# Hermes intentionally rejects distribution wheels; keep its supported source editable install.
# The complete source stays at /app/hermes-agent in both build and runtime stages.
RUN uv sync --frozen --no-dev --python /usr/local/bin/python
COPY persistence/requirements-cloud.txt /tmp/cloud-requirements.txt
RUN uv pip install --python /app/hermes-agent/.venv/bin/python -r /tmp/cloud-requirements.txt
FROM ${PYTHON_IMAGE} AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libgomp1 && rm -rf /var/lib/apt/lists/* && useradd --create-home --uid 10001 agent
COPY --from=build /app/hermes-agent /app/hermes-agent
COPY --from=tailscale /usr/local/bin/tailscale /usr/local/bin/tailscale
COPY --from=tailscale /usr/local/bin/tailscaled /usr/local/bin/tailscaled
WORKDIR /app
COPY router router
COPY telegram_bot telegram_bot
COPY assignment_skills assignment_skills
COPY proactive proactive
COPY operations operations
COPY agent_memory agent_memory
COPY persistence persistence
RUN mkdir -p /app/router/logs /app/secrets /run/hermes-profile && chown -R agent:agent /app/router/logs /home/agent
ENV PATH="/app/hermes-agent/.venv/bin:$PATH" HOME=/home/agent PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 A3_LOG_STDOUT=1 A3_REQUIRE_GCS=1 HERMES_HOME=/run/hermes-profile
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "operations.cloud_runtime"]
