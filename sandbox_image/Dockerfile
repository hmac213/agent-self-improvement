# Image for docker/daytona sandboxes: Python plus what the seed harness and the graders need.
FROM python:3.12-slim
RUN pip install --no-cache-dir anthropic pytest
ENV HOME=/agent
WORKDIR /agent
