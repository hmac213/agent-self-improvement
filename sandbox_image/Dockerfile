# Image for docker/daytona sandboxes: Python plus what the graders need (the seed harness uses only the standard library).
FROM python:3.12-slim
RUN pip install --no-cache-dir pytest
ENV HOME=/agent
WORKDIR /agent
