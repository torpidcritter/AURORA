# syntax=docker/dockerfile:1.7
# Aurora API / worker / beat image (same image, different command).
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libgomp1: OpenMP runtime used by PyTorch CPU kernels.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only PyTorch first (the PyPI default wheel pulls several GB of CUDA libraries).
COPY requirements.txt .
RUN pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu \
 && pip install -r requirements.txt

COPY alembic.ini pyproject.toml ./
COPY alembic ./alembic
COPY app ./app

RUN useradd --system --uid 10001 --home /app aurora \
 && mkdir -p /data/artifacts && chown -R aurora /data
USER aurora

ENV ARTIFACT_DIR=/data/artifacts \
    API_PORT=8100 \
    FORWARDED_ALLOW_IPS=127.0.0.1

EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,urllib.request,sys; p=os.environ.get('PORT') or os.environ.get('API_PORT','8100'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/health/live', timeout=3).status == 200 else 1)"

# --proxy-headers makes request.client the real client IP (used for rate limits and audit) when the API sits
# behind a trusted proxy; FORWARDED_ALLOW_IPS must list only that proxy's address.
# PROMETHEUS_MULTIPROC_DIR (API only) lets /metrics aggregate all uvicorn workers; it is emptied at each start.
# $PORT is honoured when a platform (e.g. Railway) assigns one.
CMD ["sh", "-c", "export PROMETHEUS_MULTIPROC_DIR=/tmp/aurora-metrics && rm -rf \"$PROMETHEUS_MULTIPROC_DIR\" && mkdir -p \"$PROMETHEUS_MULTIPROC_DIR\" && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-${API_PORT}} --workers ${WEB_CONCURRENCY:-2} --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS}\""]
