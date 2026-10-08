# Image for docker/daytona sandboxes: Python plus what the graders need (the seed harness uses only the standard library).
# The harness runs as the unprivileged `agent` user; the supervisor's own commands run as root, so its
# scratch area (/var/lib/sia: harness logs, exit codes, transfers) stays out of the agent's reach.
FROM python:3.12-slim
RUN pip install --no-cache-dir pytest \
    && useradd --uid 1000 --home-dir /agent --create-home --shell /bin/sh agent
ENV HOME=/agent
WORKDIR /agent
