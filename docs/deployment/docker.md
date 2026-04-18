# Self-Hosted Docker Deployment

The self-hosted Docker distribution is the primary supported deployment model. It runs
the full application stack — nginx, FastAPI backend, and React frontend — as Docker
Compose services.

The default setup uses HTTP on port 80 and SQLite. HTTPS and PostgreSQL are optional
overlays that can be added independently or combined.

---

## Prerequisites

| Tool | Minimum Version |
|------|----------------|
| Docker | 24.x |
| Docker Compose | v2.x (plugin, bundled with Docker Desktop) |

No local Python or Node.js installation is required.

---

## Quick Start (HTTP + SQLite)

### 1. Clone and configure

```bash
git clone https://github.com/your-org/sf-bulk-loader.git
cd sf-bulk-loader
cp .env.example .env
```

Open `.env` and set `ADMIN_USERNAME` and `ADMIN_PASSWORD` — these create the first
admin account on initial startup.

### 2. Create data directories

```bash
mkdir -p data/input data/output data/db
```

### 3. Start

```bash
docker compose up --build
```

| Service | URL |
|---------|-----|
| Web UI | http://localhost |
| API docs | http://localhost/api/docs |

Data in `data/` is persisted between restarts.

```bash
docker compose down   # stop
```

---

## Authentication

The `self_hosted` profile requires in-app authentication. Every user must log in with a
username and password before accessing the application.

The first account is created automatically on startup using the `ADMIN_USERNAME` and
`ADMIN_PASSWORD` values from `.env`. After the first user exists, these bootstrap
credentials are ignored.

Two endpoints are always public (no login required):

| Endpoint | Purpose |
|----------|---------|
| `GET /api/health` | Container health check |
| `GET /api/runtime` | Returns the active distribution profile (used by the frontend to determine whether to show the login screen) |

**SSO / OIDC** is not supported in this release. It is an explicitly planned future
enhancement.

---

## Key Management

`ENCRYPTION_KEY` and `JWT_SECRET_KEY` are auto-generated on first start and persisted
to `data/db/`:

| File | Purpose |
|------|---------|
| `data/db/encryption.key` | Fernet key — encrypts stored Salesforce credentials |
| `data/db/jwt_secret.key` | JWT signing secret — signs session tokens |

**Back up `data/db/encryption.key`.** If it is lost and `ENCRYPTION_KEY` is not set
in `.env`, stored Salesforce credentials (private keys, tokens) become unreadable and
connections must be re-configured.

**Bring your own key:** Set `ENCRYPTION_KEY` or `JWT_SECRET_KEY` in `.env` — an
explicit value always takes precedence over the auto-generated file. To generate:

```bash
# Encryption key (requires Docker — no local Python needed)
docker run --rm python:3.12-slim python -c \
  "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# JWT secret key
openssl rand -hex 32
```

---

## HTTPS Overlay

HTTPS is recommended for any deployment beyond localhost. The HTTPS overlay adds port
443, mounts TLS certificates, and redirects HTTP to HTTPS.

### 1. Provide certificates

**Option A — mkcert (recommended for local/dev use, no browser warnings):**

[mkcert](https://github.com/FiloSottile/mkcert) creates locally-trusted certificates
that work in all browsers without security warnings.

```bash
# Install mkcert (one-time)
brew install mkcert          # macOS
# Linux / Windows: see https://github.com/FiloSottile/mkcert#installation

# Install the local CA into your OS/browser trust stores (one-time per machine)
mkcert -install

# Generate a cert for localhost
mkdir -p certs
mkcert -key-file certs/key.pem -cert-file certs/cert.pem localhost 127.0.0.1 ::1
```

Replace `localhost 127.0.0.1 ::1` with your hostname or IP if accessing from other
machines on the network.

**Option B — openssl self-signed (for server/headless environments):**

```bash
mkdir -p certs
openssl req -x509 -newkey rsa:4096 -keyout certs/key.pem -out certs/cert.pem \
  -sha256 -days 825 -nodes \
  -subj "/CN=bulkloader.internal" \
  -addext "subjectAltName=DNS:bulkloader.internal,IP:your.server.ip"
```

Browsers will show a security warning. This is acceptable for server environments
where you control the client, or where a CA-signed cert is used in production.

**Option C — CA-signed cert (for production):**
Place the full certificate chain as `certs/cert.pem` (leaf cert followed by any
intermediates) and the unencrypted private key as `certs/key.pem`.

### 2. Start with HTTPS overlay

```bash
docker compose -f docker-compose.yml -f docker-compose.https.yml up --build
```

HTTPS is served on port 443. Port 80 redirects to HTTPS.

> **Note:** Automated certificate management (e.g. Let's Encrypt / Certbot) is a
> planned enhancement — see [Ticket 6](../specs/distrubution-layer-spec.md).

---

## PostgreSQL Overlay

PostgreSQL is recommended for multi-user or long-running installs.

### Bundled postgres (Docker-managed)

The overlay adds a `postgres:16` service and injects `DATABASE_URL` into the backend
automatically — **no `.env` changes are needed**.

```bash
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up --build
```

Default credentials (defined in `docker-compose.postgres.yml`):

| Variable | Default |
|----------|---------|
| `POSTGRES_DB` | `bulk_loader` |
| `POSTGRES_USER` | `bulk_loader` |
| `POSTGRES_PASSWORD` | `bulk_loader` |

Change all three values in `docker-compose.postgres.yml` before any deployment
accessible beyond localhost.

### Externally-hosted postgres (AWS RDS, managed service, etc.)

The postgres overlay is **not needed**. Set `DATABASE_URL` directly in `.env` and use
the base compose file as normal:

```
DATABASE_URL=postgresql+asyncpg://user:password@your-host:5432/bulk_loader
```

```bash
docker compose up --build
```

For RDS with SSL enforcement, append `?ssl=require` to the connection string.

---

## Combining Overlays

HTTPS and PostgreSQL overlays are independent and can be stacked:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.postgres.yml \
  -f docker-compose.https.yml \
  up --build
```

---

## Configuration Reference

All configuration is via environment variables in `.env` (loaded by Docker Compose).

### Required on first boot

| Variable | Description |
|----------|-------------|
| `ADMIN_USERNAME` | Bootstrap admin username — creates the first account. |
| `ADMIN_PASSWORD` | Bootstrap admin password — used on first startup only. |

### Full reference

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_DISTRIBUTION` | `self_hosted` | Distribution profile. Leave as `self_hosted`. |
| `APP_ENV` | `production` | `development` or `production`. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `ENCRYPTION_KEY` | _(auto-generated)_ | Fernet key for stored credential encryption. See [Key Management](#key-management). |
| `ENCRYPTION_KEY_FILE` | `/data/db/encryption.key` | Where to persist the auto-generated encryption key. |
| `JWT_SECRET_KEY` | _(auto-generated)_ | JWT signing secret. See [Key Management](#key-management). |
| `JWT_SECRET_KEY_FILE` | `/data/db/jwt_secret.key` | Where to persist the auto-generated JWT secret. |
| `ADMIN_USERNAME` | _(required on first boot)_ | Bootstrap admin username. |
| `ADMIN_PASSWORD` | _(required on first boot)_ | Bootstrap admin password. |
| `JWT_EXPIRY_MINUTES` | `60` | Session token lifetime in minutes. |
| `CORS_ORIGINS` | `["http://localhost"]` | Allowed CORS origins. Not needed for standard deployments — nginx proxies same-origin. |
| `DATABASE_URL` | `sqlite+aiosqlite:////data/db/bulk_loader.db` | SQLAlchemy connection string. |
| `SF_API_VERSION` | `v62.0` | Salesforce REST API version. |
| `SF_POLL_INTERVAL_INITIAL` | `5` | Starting poll interval (s) for Bulk API job status. |
| `SF_POLL_INTERVAL_MAX` | `30` | Maximum poll interval (s) after exponential backoff. |
| `SF_JOB_TIMEOUT_MINUTES` | `30` | Soft warning threshold for long-running jobs. Logs once at the threshold, continues polling. |
| `SF_JOB_MAX_POLL_SECONDS` | `3600` | Hard cap on a single Bulk API job's poll loop. When exceeded the job is marked failed (best-effort `abort_job` on Salesforce) and the run continues with remaining partitions. Set to `0` to disable (unbounded polling). |
| `DEFAULT_PARTITION_SIZE` | `10000` | Records per Bulk API job partition. |
| `MAX_PARTITION_SIZE` | `100000000` | Hard upper limit on partition size. |
| `INPUT_DIR` | `/data/input` | Container path for source CSVs (read-only). |
| `OUTPUT_DIR` | `/data/output` | Container path for result files. |
| `FRONTEND_BASE_URL` | _(empty)_ | Public base URL of the frontend (e.g. `https://bulkloader.example.com`). **Required** for password-reset and email-change verification links. Falls back to the HTTP request origin if not set, but explicit configuration is strongly recommended. |
| `PASSWORD_RESET_TTL_MINUTES` | `15` | Lifetime of a password-reset token in minutes. |
| `EMAIL_CHANGE_TTL_MINUTES` | `30` | Lifetime of an email-change verification token in minutes. |
| `PW_RESET_RATE_LIMIT_PER_IP_HOUR` | `10` | Maximum password-reset requests per IP address per hour. |
| `PW_RESET_RATE_LIMIT_PER_EMAIL_HOUR` | `5` | Maximum password-reset requests per email address per hour. |
| `EMAIL_CHANGE_RATE_LIMIT_PER_USER_HOUR` | `3` | Maximum email-change requests per authenticated user per hour. |

### Volume mounts

| Host path       | Container path | Access     | Purpose                                         |
| --------------- | -------------- | ---------- | ----------------------------------------------- |
| `./data/input`  | `/data/input`  | Read-only  | Source CSV files                                |
| `./data/output` | `/data/output` | Read-write | Success/error result CSVs                       |
| `./data/db`     | `/data/db`     | Read-write | SQLite database + auto-generated encryption and JWT key files |

When using the HTTPS overlay:

| Host path | Container path | Access | Purpose |
|-----------|---------------|--------|---------|
| `./certs` | `/etc/nginx/certs` | Read-only | TLS certificate and private key |

---

## Troubleshooting

### Backend fails to start — cannot write encryption key

The backend auto-generates `ENCRYPTION_KEY` on first start and writes it to `data/db/encryption.key`.
This fails if the `data/db` directory does not exist or is not writable by the container.

```bash
mkdir -p data/db data/input data/output
docker compose up
```

If you want to supply your own key instead:

```bash
docker run --rm python:3.12-slim python -c \
  "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set the output as `ENCRYPTION_KEY=<key>` in `.env`.

### Backend fails to start — database locked

SQLite WAL mode may leave lock files if the backend crashed. Remove them and restart:
```bash
rm -f data/db/bulk_loader.db-wal data/db/bulk_loader.db-shm
docker compose up
```

### nginx fails to start — certificate not found

Ensure `certs/cert.pem` and `certs/key.pem` exist when using the HTTPS overlay.

### Frontend shows a blank page

- Check `docker compose ps` — the backend may have failed its health check.
- Rebuild: `docker compose up --build`.

### Port conflict on 80 or 443

Set `HTTP_PORT` or `HTTPS_PORT` in `.env` (defaults: 80 and 443):

```
HTTP_PORT=8080
HTTPS_PORT=8443
```

These values control only the host-side binding — the application continues to listen
on 80/443 inside the container.

### Viewing logs

```bash
docker compose logs -f            # all services
docker compose logs -f backend    # backend only
docker compose logs -f frontend   # nginx only
```
