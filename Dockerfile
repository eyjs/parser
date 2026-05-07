FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY docforge/ ./docforge/

RUN pip install --no-cache-dir ".[web,morpheme,cloud_vlm]" gunicorn

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 5051

CMD ["gunicorn", "docforge.web.app:create_app()", \
     "--bind", "0.0.0.0:5051", \
     "--worker-class", "gthread", \
     "--threads", "16", \
     "--workers", "2", \
     "--timeout", "300"]
