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

# Optionally run Alembic migrations, then start the server.
#
# RUN_MIGRATIONS=true   (default) — migrate inline before starting uvicorn.
#                       This is the right default for self-hosted Docker
#                       compose where exactly one container runs at a time.
# RUN_MIGRATIONS=false  — skip migrations; assume they have already run via
#                       a separate one-shot task. The aws_hosted ECS service
#                       task definition sets this so concurrent service tasks
#                       never race on `alembic upgrade head`. The companion
#                       MigrationTaskDefinition runs migrations out-of-band
#                       via `aws ecs run-task` before the service rollout.
#                       See SFBL-277 / docs/deployment/aws.md "Ongoing
#                       Deployments" for the deploy sequence.
CMD ["sh", "-c", "if [ \"${RUN_MIGRATIONS:-true}\" = \"true\" ]; then alembic upgrade head; fi && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
