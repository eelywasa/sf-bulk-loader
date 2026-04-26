FROM python:3.12-slim

# curl is needed for the Docker Compose health check
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Run as non-root for security (spec §11)
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Bake version provenance into the image at build time.
# Operators supply these via: GIT_SHA=$(git rev-parse --short HEAD) BUILD_TIME=$(date -u +%FT%TZ) docker compose build
# CI supplies them automatically via the version-computation workflow step.
ARG APP_VERSION=0.0.0-dev
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ENV APP_VERSION=$APP_VERSION \
    APP_GIT_SHA=$GIT_SHA \
    APP_BUILD_TIME=$BUILD_TIME

# Input volume is read-only; output + db volumes are writable
RUN mkdir -p /data/input /data/output /data/db && \
    chown -R appuser:appuser /data /app

USER appuser

EXPOSE 8000

# Run Alembic migrations then start the server
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
