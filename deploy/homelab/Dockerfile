# Homelab prediction API image. Build from the repository root:
#   docker compose -f deploy/homelab/docker-compose.yml build
FROM python:3.11-slim-bookworm

RUN apt-get update \
  && apt-get install -y --no-install-recommends curl \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY services/prediction_api/requirements.txt /app/services/prediction_api/requirements.txt
RUN pip install --no-cache-dir -r /app/services/prediction_api/requirements.txt

COPY model.pkl /app/model.pkl
COPY services/prediction_api /app/services/prediction_api

WORKDIR /app/services/prediction_api
ENV MODEL_PATH=/app/model.pkl \
    UPLOAD_JOB_DIR=/var/lib/csa/upload-jobs \
    PORT=8000

RUN mkdir -p /var/lib/csa/upload-jobs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/health" || exit 1

CMD ["sh", "-c", "python -m steamlink.migrate --if-configured && exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
