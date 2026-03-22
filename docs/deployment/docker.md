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

### 2. Generate required secrets

```bash
# Encryption key (encrypts stored Salesforce credentials)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# JWT secret key (signs session tokens)
python -c "import secrets; print(secrets.token_hex(32))"
```

Set `ENCRYPTION_KEY`, `JWT_SECRET_KEY`, `ADMIN_USERNAME`, and `ADMIN_PASSWORD` in `.env`.

### 3. Create data directories

```bash
mkdir -p data/input data/output data/db
```

### 4. Start

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

## HTTPS Overlay

HTTPS is recommended for any deployment beyond localhost. The HTTPS overlay adds port
443, mounts TLS certificates, and redirects HTTP to HTTPS.

### 1. Provide certificates

**Self-signed (for internal/dev use):**
```bash
mkdir certs
openssl req -x509 -newkey rsa:4096 -keyout certs/key.pem -out certs/cert.pem \
  -sha256 -days 825 -nodes \
  -subj "/CN=bulkloader.internal" \
  -addext "subjectAltName=DNS:bulkloader.internal,IP:your.server.ip"
```
Browsers will show a security warning. Add the cert to your OS/browser trust store to
suppress it on internal networks. (Modern browsers require the SAN extension — the
`-addext` flag above ensures it is present.)

**CA-signed cert:**
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

PostgreSQL is recommended for multi-user or long-running installs. The overlay adds a
`postgres:16` service and sets `DATABASE_URL` automatically.

```bash
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up --build
```

Default credentials are in `docker-compose.postgres.yml` — change them before any
exposed deployment.

For externally-hosted PostgreSQL (e.g. AWS RDS), set `DATABASE_URL` directly in `.env`:

```
DATABASE_URL=postgresql+asyncpg://user:password@your-host:5432/bulk_loader
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
| `ENCRYPTION_KEY` | 32-byte URL-safe base64 Fernet key. |
| `JWT_SECRET_KEY` | Random secret for signing session JWTs. |
| `ADMIN_USERNAME` | Bootstrap admin username — creates the first account. |
| `ADMIN_PASSWORD` | Bootstrap admin password — used on first startup only. |

### Full reference

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_DISTRIBUTION` | `self_hosted` | Distribution profile. Leave as `self_hosted`. |
| `APP_ENV` | `production` | `development` or `production`. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `ENCRYPTION_KEY` | _(required)_ | Fernet key for stored credential encryption. |
| `JWT_SECRET_KEY` | _(required)_ | JWT signing secret. |
| `ADMIN_USERNAME` | _(required on first boot)_ | Bootstrap admin username. |
| `ADMIN_PASSWORD` | _(required on first boot)_ | Bootstrap admin password. |
| `JWT_EXPIRY_MINUTES` | `60` | Session token lifetime in minutes. |
| `CORS_ORIGINS` | `["http://localhost"]` | Allowed CORS origins. Not needed for standard deployments — nginx proxies same-origin. |
| `DATABASE_URL` | `sqlite+aiosqlite:////data/db/bulk_loader.db` | SQLAlchemy connection string. |
| `SF_API_VERSION` | `v62.0` | Salesforce REST API version. |
| `SF_POLL_INTERVAL_INITIAL` | `5` | Starting poll interval (s) for Bulk API job status. |
| `SF_POLL_INTERVAL_MAX` | `30` | Maximum poll interval (s) after exponential backoff. |
| `SF_JOB_TIMEOUT_MINUTES` | `30` | Warning threshold for long-running jobs. |
| `DEFAULT_PARTITION_SIZE` | `10000` | Records per Bulk API job partition. |
| `MAX_PARTITION_SIZE` | `100000000` | Hard upper limit on partition size. |
| `INPUT_DIR` | `/data/input` | Container path for source CSVs (read-only). |
| `OUTPUT_DIR` | `/data/output` | Container path for result files. |

### Volume mounts

| Host path | Container path | Access | Purpose |
|-----------|---------------|--------|---------|
| `./data/input` | `/data/input` | Read-only | Source CSV files |
| `./data/output` | `/data/output` | Read-write | Success/error result CSVs |
| `./data/db` | `/data/db` | Read-write | SQLite database (ignored when using PostgreSQL) |

When using the HTTPS overlay:

| Host path | Container path | Access | Purpose |
|-----------|---------------|--------|---------|
| `./certs` | `/etc/nginx/certs` | Read-only | TLS certificate and private key |

---

## Troubleshooting

### Backend fails to start — `ENCRYPTION_KEY` missing or invalid

Generate a valid Fernet key:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

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

Edit the host-side port in the relevant Compose file:
```yaml
# docker-compose.yml — change HTTP port
ports:
  - "8080:80"

# docker-compose.https.yml — change HTTPS port
ports:
  - "8443:443"
```

### Viewing logs

```bash
docker compose logs -f            # all services
docker compose logs -f backend    # backend only
docker compose logs -f frontend   # nginx only
```
