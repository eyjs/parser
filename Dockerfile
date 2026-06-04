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

CMD gunicorn "docforge.web.app:create_app()" \
     --bind 0.0.0.0:5051 \
     --worker-class gthread \
     --threads ${DOCFORGE_THREADS} \
     --workers ${DOCFORGE_WORKERS} \
     --timeout ${DOCFORGE_GUNICORN_TIMEOUT}
