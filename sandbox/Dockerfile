# syntax=docker/dockerfile:1
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SANDBOX_BASE_DIR=/workspace \
    SANDBOX_EXEC_TIMEOUT_SECONDS=60 \
    HOME=/workspace \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib \
    XDG_CACHE_HOME=/tmp/cache \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1

WORKDIR /opt/runtime

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --only-binary=:all: --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && groupadd --gid 1000 sandbox \
    && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /workspace sandbox \
    && mkdir -p /workspace \
    && chown 1000:1000 /workspace

# Match the cluster's controller release. Verify source even if the tag moves.
ADD --checksum=sha256:62f6bbac801fe9529618a06a83e4a613ba77eb66236dc871dc1fef38f4e9a531 \
    https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/v1.0.10/examples/python-runtime-sandbox/main.py /opt/runtime/main.py
RUN chmod 0444 /opt/runtime/main.py

USER 1000:1000
WORKDIR /workspace
EXPOSE 8888

# emptyDir hides the image's workspace contents; initialize output on every start.
CMD ["/bin/sh", "-c", "mkdir -p /workspace/output && exec uvicorn main:app --app-dir /opt/runtime --host 0.0.0.0 --port 8888"]
