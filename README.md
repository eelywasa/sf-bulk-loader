# Salesforce Bulk Loader

A containerized web application for orchestrating large-scale data loads into Salesforce
using the **Bulk API 2.0**. Define multi-object load plans, track job progress in real
time, and capture success/error logs — all through a browser-based UI.

---

## Architecture

```
Browser ──▶ nginx (port 80, HTTP — default)
               │         (port 443, HTTPS — optional overlay)
               ├─▶ /api/*   ──▶ FastAPI backend (internal)
               ├─▶ /ws/*    ──▶ FastAPI WebSocket
               └─▶ /*       ──▶ React SPA (static files)
                                      │
                               SQLite (default) or PostgreSQL (optional overlay)
```

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic |
| Database | SQLite (WAL mode) — default; PostgreSQL supported |
| Frontend | React 18, Vite, TypeScript, Tailwind CSS |
| Proxy | nginx 1.27 |
| Packaging | Docker, Docker Compose |

---

## Quick Start

```bash
git clone https://github.com/eelywasa/sf-bulk-loader.git
cd sf-bulk-loader
cp .env.example .env          # set ADMIN_USERNAME and ADMIN_PASSWORD
mkdir -p data/input data/output data/db
docker compose up --build
```

Open **http://localhost**.

For HTTPS, PostgreSQL, or combined setups see [docs/deployment/docker.md](docs/deployment/docker.md).

---

## Documentation

**Architecture & design**

| Document | Description |
|----------|-------------|
| [docs/architecture.md](docs/architecture.md) | System overview — backend, frontend, data model, distribution profiles |
| [docs/architecture/auth-and-rbac.md](docs/architecture/auth-and-rbac.md) | Auth modes, JWT sessions, RBAC model, invitations |
| [docs/architecture/run-execution.md](docs/architecture/run-execution.md) | Orchestrator, partitioning, Salesforce Bulk API, polling, aborts |
| [docs/architecture/storage.md](docs/architecture/storage.md) | Input discovery, output sinks, encryption at rest |

**Operations & developer**

| Document | Description |
|----------|-------------|
| [docs/deployment/docker.md](docs/deployment/docker.md) | Self-hosted Docker deployment — configuration, HTTPS, PostgreSQL, troubleshooting |
| [docs/deployment/desktop.md](docs/deployment/desktop.md) | Desktop (Electron) deployment |
| [docs/deployment/aws.md](docs/deployment/aws.md) | AWS-hosted deployment |
| [docs/development.md](docs/development.md) | Local development, running tests, database migrations |
| [docs/admin-recovery.md](docs/admin-recovery.md) | Break-glass CLI for locked-out admins |
| [docs/observability.md](docs/observability.md) | Event taxonomy, metrics, spans, DoD checklist |
| [docs/ci.md](docs/ci.md) | CI workflow topology |
| [docs/email.md](docs/email.md) | Outbound email — backend selection, SMTP credentials, delivery log, troubleshooting |

**Usage**

| Document | Description |
|----------|-------------|
| [docs/usage/](docs/usage/index.md) | Using the app — Salesforce setup, CSV format, load plans, notifications, user management |
| [docs/salesforce-jwt-setup.md](docs/salesforce-jwt-setup.md) | Detailed Salesforce Connected App setup |
| [docs/s3-connection-setup.md](docs/s3-connection-setup.md) | S3 input source configuration |

---

## Distribution Profiles

The app supports three deployment profiles, selected via `APP_DISTRIBUTION`:

| Profile | Auth | Transport | Database |
|---------|------|-----------|----------|
| `self_hosted` (default) | Local in-app login | HTTP or HTTPS | SQLite or PostgreSQL |
| `desktop` | None (no login) | Local loopback | SQLite only |
| `aws_hosted` | Local in-app login | HTTPS required | PostgreSQL required |

---

## Project Structure

```
sf-bulk-loader/
├── docker-compose.yml           # HTTP + SQLite (default)
├── docker-compose.https.yml     # Overlay: HTTPS
├── docker-compose.postgres.yml  # Overlay: PostgreSQL
├── .env.example
├── backend/
│   ├── app/
│   │   ├── config.py            # Pydantic settings + distribution profile validation
│   │   ├── main.py              # FastAPI app, CORS, router registration
│   │   ├── database.py          # Async SQLAlchemy engine
│   │   ├── models/              # ORM models
│   │   ├── schemas/             # Pydantic request/response schemas
│   │   ├── api/                 # Route handlers
│   │   ├── services/            # Orchestrator, Salesforce auth/bulk, CSV processing
│   │   └── utils/               # WebSocket manager
│   └── alembic/                 # Database migrations
├── frontend/
│   ├── nginx.conf               # HTTP nginx config (baked into image)
│   ├── nginx.https.conf         # HTTPS nginx config (mounted by HTTPS overlay)
│   └── src/
├── docs/
│   ├── deployment/              # Deployment guides per distribution
│   ├── specs/                   # Architecture and feature specs
│   └── ...
└── data/
    ├── input/                   # Source CSV files
    ├── output/                  # Result files
    └── db/                      # SQLite database
```
