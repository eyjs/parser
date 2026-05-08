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

RUN pip install --no-cache-dir ".[web,morpheme,cloud_vlm]" gunicorn

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 5051

CMD ["gunicorn", "docforge.web.app:create_app()", \
     "--bind", "0.0.0.0:5051", \
     "--worker-class", "gthread", \
     "--threads", "16", \
     "--workers", "1", \
     "--timeout", "300"]
