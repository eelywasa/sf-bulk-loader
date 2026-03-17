# Salesforce Bulk Loader

A containerized web application for orchestrating large-scale data loads into Salesforce using the **Bulk API 2.0**. Define multi-object load plans, track job progress in real time, and capture success/error logs — all through a browser-based UI.

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Prerequisites](#prerequisites)
- [Quick Start (Docker)](#quick-start-docker)
- [Configuration](#configuration)
- [Salesforce Connected App Setup](#salesforce-connected-app-setup)
- [Loading Data](#loading-data)
- [Local Development](#local-development)
- [Running Tests](#running-tests)
- [Directory Structure](#directory-structure)
- [Troubleshooting](#troubleshooting)

---

## Architecture Overview

```
Browser ──▶ nginx (port 443, HTTPS)
               │  (port 80 redirects to HTTPS)
               ├─▶ /api/*   ──▶ FastAPI backend (port 8000)
               ├─▶ /ws/*    ──▶ FastAPI WebSocket
               └─▶ /*       ──▶ React SPA (static files)
                                      │
                               SQLite database
                               /data/db/bulk_loader.db
```

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.12, FastAPI, SQLAlchemy, Alembic |
| Database | SQLite (WAL mode) |
| Frontend | React 18, Vite, TypeScript, Tailwind CSS |
| Proxy/Serve | nginx 1.27 |
| Containerisation | Docker, Docker Compose |

---

## Prerequisites

| Tool | Minimum Version | Install |
|------|----------------|---------|
| Docker | 24.x | [docs.docker.com](https://docs.docker.com/get-docker/) |
| Docker Compose | v2.x (plugin) | Bundled with Docker Desktop |

No local Python or Node.js installation is required to run the application via Docker.

For local development (optional):

| Tool | Minimum Version |
|------|----------------|
| Python | 3.12 |
| Node.js | 20.x |
| npm | 10.x |

---

## Quick Start (Docker)

### 1. Clone the repository

```bash
git clone https://github.com/your-org/sf-bulk-loader.git
cd sf-bulk-loader
```

### 2. Create your environment file

```bash
cp .env.example .env
```

Open `.env` and fill in the required values (see [Configuration](#configuration)).

### 3. Generate an encryption key

The application encrypts stored Salesforce credentials at rest. Generate a key and paste it into `.env`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set the output as the value of `ENCRYPTION_KEY` in `.env`.

### 4. Create the data directories

```bash
mkdir -p data/input data/output data/db
```

Place your source CSV files in `data/input/`.

### 5. Provide TLS certificates

nginx serves the application over HTTPS and requires a certificate and private key.

**Option A — Self-signed (internal/dev use):**
```bash
mkdir certs
openssl req -x509 -newkey rsa:4096 -keyout certs/key.pem -out certs/cert.pem \
  -sha256 -days 825 -nodes \
  -subj "/CN=bulkloader.internal" \
  -addext "subjectAltName=DNS:bulkloader.internal,IP:your.server.ip"
```
Browsers will show a security warning for self-signed certs. Add the cert to your OS/browser trust store to suppress the warning on internal networks.

**Option B — CA-signed cert:**
Place your certificate chain as `certs/cert.pem` and the unencrypted private key as `certs/key.pem`. The cert file should contain the leaf cert followed by any intermediate certs (full chain PEM format).

docker-compose.yml mounts `./certs` into the nginx container read-only. If the files are absent at startup, nginx will exit with a clear error message.

### 6. Build and start the application

```bash
docker compose up --build
```

The first build downloads base images and installs dependencies. Subsequent starts are faster.

| Service | URL |
|---------|-----|
| Web UI | https://localhost |
| API (direct) | http://localhost:8000 |
| API docs (Swagger) | http://localhost:8000/docs |

### 7. Stop the application

```bash
docker compose down
```

Data in `data/` is persisted on your host machine between restarts.

---

## Configuration

All configuration is provided via environment variables loaded from `.env` by Docker Compose. Copy `.env.example` to `.env` and edit the values.

### Required Variables

| Variable | Description |
|----------|-------------|
| `ENCRYPTION_KEY` | Fernet key for encrypting stored credentials. Generate with the command in [Step 3](#3-generate-an-encryption-key). |

### Full Variable Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_ENV` | `production` | `development` or `production`. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `ENCRYPTION_KEY` | _(required)_ | 32-byte URL-safe base64 key. |
| `CORS_ORIGINS` | `["http://localhost:3000","https://localhost:3000"]` | JSON array of allowed CORS origins. Set to your HTTPS URL in production. |
| `DATABASE_URL` | `sqlite+aiosqlite:////data/db/bulk_loader.db` | SQLAlchemy connection string. |
| `SF_API_VERSION` | `v62.0` | Salesforce REST API version. |
| `SF_POLL_INTERVAL_INITIAL` | `5` | Starting poll interval (seconds) for Bulk API job status. |
| `SF_POLL_INTERVAL_MAX` | `30` | Maximum poll interval (seconds) after backoff. |
| `SF_JOB_TIMEOUT_MINUTES` | `30` | Log a warning if a job remains in-progress beyond this threshold. |
| `DEFAULT_PARTITION_SIZE` | `10000` | Records per Bulk API job partition. |
| `MAX_PARTITION_SIZE` | `100000000` | Hard upper limit on partition size. |
| `INPUT_DIR` | `/data/input` | Container path for source CSVs (mounted read-only). |
| `OUTPUT_DIR` | `/data/output` | Container path for success/error result files. |

### Volume Mounts

Docker Compose bind-mounts three host directories into the backend container:

| Host Path | Container Path | Access | Purpose |
|-----------|---------------|--------|---------|
| `./data/input` | `/data/input` | Read-only | Source CSVs |
| `./data/output` | `/data/output` | Read-write | Result files |
| `./data/db` | `/data/db` | Read-write | SQLite database |
| `./certs` | `/etc/nginx/certs` | Read-only | TLS certificate and key for nginx HTTPS |

- **`data/input`**: Drop source CSV files here before starting a load run. The application never writes to this directory.
- **`data/output`**: Success, error, and unprocessed result CSVs are written here after each Salesforce Bulk API job completes.
- **`data/db`**: Contains `bulk_loader.db` (SQLite). Back this up if you want to preserve load history.

---

## Salesforce Connected App Setup

The application authenticates to Salesforce using the **OAuth 2.0 JWT Bearer** flow (server-to-server, no interactive login). This requires a Connected App in your Salesforce org.

### Step 1: Generate an RSA key pair

```bash
# Generate private key (2048-bit RSA)
openssl genrsa -out server.key 2048

# Extract the public certificate
openssl req -new -x509 -key server.key -out server.crt -days 365 \
  -subj "/CN=sf-bulk-loader"
```

Keep `server.key` secure. You will paste its contents into the application. Never commit it to version control.

### Step 2: Create a Connected App

1. In Salesforce Setup, search for **App Manager** and click **New Connected App**.
2. Fill in the basic information (name, API name, contact email).
3. Under **API (Enable OAuth Settings)**:
   - Enable OAuth Settings: **checked**
   - Callback URL: `https://localhost` (unused, but required by the form)
   - Selected OAuth Scopes: `Manage user data via APIs (api)`, `Perform requests at any time (refresh_token, offline_access)`
   - Enable for Device Flow: **unchecked**
   - Use digital signatures: **checked** — upload `server.crt`
4. Save and note the **Consumer Key** (this is your `client_id`).

### Step 3: Approve the Connected App

After saving, Salesforce may require you to approve the app:

- Go to **Setup → Manage Connected Apps → Policies** for your app.
- Set **Permitted Users** to "Admin approved users are pre-authorized".
- Under **Profiles** or **Permission Sets**, add the profile/permission set of the user that will run the loads.

### Step 4: Configure the connection in the app

1. Open the web UI at https://localhost.
2. Navigate to **Connection Manager**.
3. Create a new connection:
   - **Name**: a friendly label (e.g., "Production")
   - **Login URL**: `https://login.salesforce.com` (or `https://test.salesforce.com` for sandboxes)
   - **Client ID**: your Connected App Consumer Key
   - **Username**: the Salesforce username that will run the loads
   - **Private Key**: paste the full contents of `server.key` (including `-----BEGIN RSA PRIVATE KEY-----` headers)
   - **Is Sandbox**: toggle on for sandboxes/scratch orgs
4. Click **Test Connection** to verify authentication works.

For more detail, see [`docs/salesforce-jwt-setup.md`](docs/salesforce-jwt-setup.md).

---

## Loading Data

### Prepare your CSV files

Place CSV files in `data/input/`. Files must:

- Use UTF-8 encoding (latin-1 and CP-1252 are auto-converted).
- Use `\n` (LF) line endings.
- Have Salesforce field API names as column headers.
- Use `#N/A` to null out a field value.

For child objects that reference parents via external IDs, use relationship notation in the header:

```csv
FirstName,LastName,Email,Account.ExternalId__c
Jane,Doe,jane@example.com,ACCT-001
```

### Create a Load Plan

1. Go to **Load Plans** in the UI and create a new plan.
2. Select the target Salesforce connection.
3. Add **Load Steps** in execution order (parent objects before child objects):
   - **Object Name**: Salesforce API name (e.g., `Account`, `Contact`)
   - **Operation**: `insert`, `update`, `upsert`, or `delete`
   - **External ID Field**: required for `upsert` (e.g., `ExternalId__c`)
   - **CSV File Pattern**: glob pattern matching files in `data/input/` (e.g., `accounts_*.csv`)
   - **Partition Size**: records per Bulk API job (default 10,000)
4. Use the **Preview** button to verify file matching and record counts before running.

### Run the plan

Click **Run** on the Load Plan page. Monitor progress in real time on the Load Run view.

Result files are written to `data/output/` with the pattern:
```
{run_id}/{step_id}/{job_id}_success.csv
{run_id}/{step_id}/{job_id}_error.csv
{run_id}/{step_id}/{job_id}_unprocessed.csv
```

---

## Local Development

To run the backend and frontend outside Docker (useful for fast iteration):

### Backend

```bash
cd backend

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy and configure the local env file
cp .env.example .env
# Edit .env — set ENCRYPTION_KEY and adjust paths if needed

# Apply database migrations
alembic upgrade head

# Start the development server (auto-reloads on file changes)
uvicorn app.main:app --reload
```

Backend available at http://localhost:8000. Interactive API docs at http://localhost:8000/docs.

### Frontend

```bash
cd frontend

# Install dependencies
npm install

# Start the Vite development server
npm run dev
```

Frontend available at http://localhost:5173. API calls are proxied to `http://localhost:8000` via the Vite dev server config.

Set `APP_ENV=development` in your backend `.env` so CORS allows the Vite dev origin.

### Database migrations

```bash
cd backend

# Apply all pending migrations
alembic upgrade head

# Create a new migration after changing SQLAlchemy models
alembic revision --autogenerate -m "describe your change"
```

---

## Running Tests

### Backend

```bash
cd backend
pytest
```

Tests use an in-memory SQLite database and do not require a real Salesforce connection. Async tests are run via `pytest-asyncio`.

```bash
# Run with verbose output
pytest -v

# Run a specific test file
pytest tests/test_csv_processor.py
```

### Frontend

```bash
cd frontend

# Run tests in watch mode
npm test

# Single run (for CI)
npm run test:run

# Type checking
npm run typecheck
```

---

## Directory Structure

```
sf-bulk-loader/
├── docker-compose.yml          # Compose definition for all services
├── .env.example                # Template for required environment variables
├── README.md
├── certs/                      # TLS cert and key (not committed — add to .gitignore)
│   ├── cert.pem
│   └── key.pem
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── alembic/                # Database migration scripts
│   ├── alembic.ini
│   └── app/
│       ├── main.py             # FastAPI application entrypoint
│       ├── config.py           # Settings loaded from environment
│       ├── database.py         # SQLAlchemy engine and session factory
│       ├── models/             # SQLAlchemy ORM models
│       ├── schemas/            # Pydantic request/response schemas
│       ├── api/                # FastAPI route handlers
│       ├── services/           # Business logic (auth, Bulk API, orchestrator)
│       └── utils/
├── frontend/
│   ├── Dockerfile
│   ├── nginx.conf              # nginx config — SPA routing + API proxy
│   ├── package.json
│   └── src/
│       ├── App.tsx
│       ├── pages/
│       └── components/
├── data/
│   ├── input/                  # Drop source CSVs here (read-only mount)
│   ├── output/                 # Result files written here
│   └── db/                     # SQLite database file
└── docs/
    └── salesforce-jwt-setup.md
```

---

## Troubleshooting

### `ENCRYPTION_KEY` is missing or invalid

```
ValueError: ENCRYPTION_KEY must be set
```

Generate a new key and add it to `.env`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Backend fails to start — database locked

SQLite uses WAL mode for concurrent reads. If the backend crashes without cleanly shutting down, a lock file (`bulk_loader.db-wal`, `bulk_loader.db-shm`) may remain. Remove these files from `data/db/` and restart.

### Salesforce authentication error — `invalid_grant`

- Verify the **Consumer Key** matches the Connected App exactly.
- Confirm the **username** has been granted the Connected App via a Profile or Permission Set.
- Check that the system clock on the Docker host is accurate (JWT `exp` claims are time-sensitive).
- For sandboxes, ensure **Login URL** is set to `https://test.salesforce.com`.

### Frontend shows a blank page

Check browser console for errors. Common causes:
- The backend is not running or failed its health check — `docker compose ps` to inspect status.
- Stale build artifacts — rebuild with `docker compose up --build`.

### Port conflict on 80, 443, or 8000

Edit `docker-compose.yml` to change the host-side ports:

```yaml
ports:
  - "8443:443"   # frontend HTTPS on port 8443
  - "8080:80"    # HTTP redirect on port 8080
```

### Viewing logs

```bash
# Follow all service logs
docker compose logs -f

# Backend logs only
docker compose logs -f backend

# Frontend (nginx) logs only
docker compose logs -f frontend
```
