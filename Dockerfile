# Stage 1: Build Vue frontend
FROM node:22-slim AS frontend
WORKDIR /frontend
COPY docforge/web/frontend/package.json docforge/web/frontend/package-lock.json* ./
RUN npm ci
COPY docforge/web/frontend/ .
RUN npm run build

# Stage 2: Python application
FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY docforge/ ./docforge/

# Copy Vue build output into the frontend dist directory
COPY --from=frontend /frontend/dist ./docforge/web/frontend/dist

RUN pip install --no-cache-dir ".[web,morpheme,cloud_vlm,easyocr]" gunicorn

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 5051

# Gunicorn settings tunable via env. --timeout raised from 300 to 1800 so large
# born-digital documents (1500-page insurance 약관) are not killed mid-parse; it
# must stay above DOCFORGE_SYNC_TIMEOUT. Shell-form CMD so ${...} is expanded.
ENV DOCFORGE_WORKERS=1
ENV DOCFORGE_THREADS=16
ENV DOCFORGE_GUNICORN_TIMEOUT=1800

# Execution-model split (defects A & B). CPU-bound async parsing now runs in a
# SEPARATE process pool (the `docforge-worker` entrypoint / compose service),
# NOT inside this gunicorn web process -- a long parse can no longer hold the
# GIL and starve HTTP submit/poll ("Server disconnected"). The web process only
# enqueues and polls, so DOCFORGE_INPROC_WORKER defaults to 0 here. The worker
# service shares the SAME image and the SAME DOCFORGE_ASYNC_STORE_DIR volume;
# single-node multi-process is the premise (SQLite WAL + atomic claim are safe
# across processes on one host). DOCFORGE_PARSE_WORKERS sizes that pool and
# DOCFORGE_QUEUE_MAX is the backpressure ceiling (503 + Retry-After on overflow).
ENV DOCFORGE_INPROC_WORKER=0
ENV DOCFORGE_PARSE_WORKERS=4
ENV DOCFORGE_QUEUE_MAX=16
ENV DOCFORGE_RETRY_AFTER_SEC=5

# This image serves BOTH roles by overriding the command:
#   - web    (default CMD below): gunicorn HTTP server, enqueue/poll only
#   - worker (compose `docforge-worker` service): command `docforge-worker`,
#            the parse-worker process pool that consumes the durable queue.
CMD gunicorn "docforge.web.app:create_app()" \
     --bind 0.0.0.0:5051 \
     --worker-class gthread \
     --threads ${DOCFORGE_THREADS} \
     --workers ${DOCFORGE_WORKERS} \
     --timeout ${DOCFORGE_GUNICORN_TIMEOUT}
