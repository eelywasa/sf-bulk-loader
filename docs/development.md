# Local Development

---

## Backend

```bash
cd backend

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp ../.env.example .env
# Set ENCRYPTION_KEY, JWT_SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD

# Apply database migrations
alembic upgrade head

# Start the development server (auto-reloads on file changes)
uvicorn app.main:app --reload
```

Backend: http://localhost:8000
API docs (Swagger): http://localhost:8000/docs

---

## Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend: http://localhost:5173
API calls are proxied to `http://localhost:8000` via the Vite dev server config.

Set `APP_ENV=development` in the backend `.env` to allow the Vite dev origin through CORS.

---

## Database Migrations

```bash
cd backend

# Apply all pending migrations
alembic upgrade head

# Create a new migration after changing SQLAlchemy models
alembic revision --autogenerate -m "describe your change"
```

---

## PyInstaller (desktop binary)

The Electron app bundles a compiled backend binary rather than raw Python source.
This makes the packaged app self-contained — no Python installation required on the
user's machine.

### Prerequisites

```bash
cd backend
pip install -r requirements-desktop.txt   # slim deps — no asyncpg, no pytest
pip install pyinstaller
```

### Build the binary

```bash
cd backend
pyinstaller sf_bulk_loader.spec --clean --noconfirm
# Output: backend/dist/sf_bulk_loader/  (folder with executable + shared libs)
```

When running the desktop app from source, Electron looks for backend tools in the backend virtualenv:
- macOS/Linux: `backend/.venv/bin/uvicorn`, `backend/.venv/bin/alembic`
- Windows: `backend/.venv/Scripts/uvicorn.exe`, `backend/.venv/Scripts/alembic.exe`

### Test the binary

```bash
# Verify --migrate works (creates the full schema in a temp DB)
DATABASE_URL="sqlite+aiosqlite:////tmp/test.db" \
ENCRYPTION_KEY_FILE=/tmp/enc.key \
JWT_SECRET_KEY_FILE=/tmp/jwt.key \
APP_DISTRIBUTION=desktop \
./dist/sf_bulk_loader/sf_bulk_loader --migrate

# Verify the server starts
DATABASE_URL="sqlite+aiosqlite:////tmp/test.db" \
ENCRYPTION_KEY_FILE=/tmp/enc.key \
JWT_SECRET_KEY_FILE=/tmp/jwt.key \
APP_DISTRIBUTION=desktop \
./dist/sf_bulk_loader/sf_bulk_loader &
curl http://127.0.0.1:8000/api/health
```

### Notes

- `backend/dist/sf_bulk_loader/` is gitignored — it is rebuilt by CI on every release
- The binary is self-contained: no Python required on the target machine
- `asyncpg` is intentionally excluded (SQLite-only on desktop; not imported in the app tree)
- `boto3` is bundled (~50 MB) because it is imported at module level in `input_connections.py`
- Adding a new migration: add the version file to `backend/alembic/versions/` — the `alembic/`
  directory is bundled into the binary's `_MEIPASS` at build time, so no spec changes needed

---

## Observability

### Structured logging

Set `LOG_FORMAT=json` in `.env` to enable structured JSON logging (one JSON object per line on stdout). This is the default for deployed environments. Local development uses plain text by default.

Set `LOG_LEVEL=DEBUG` to see detailed request and workflow logs.

### Health endpoints

Three health endpoints are available:

- `GET /api/health/live` — liveness probe (no dependency checks, always fast)
- `GET /api/health/ready` — readiness probe (checks database connectivity; returns 503 if unavailable)
- `GET /api/health/dependencies` — operator view of per-dependency health

Docker Compose uses `/api/health/ready` for its health check. The legacy `/api/health` endpoint is preserved for backward compatibility.

### Optional tracing

OpenTelemetry-compatible tracing can be enabled via `.env`:

```env
TRACING_ENABLED=true
TRACE_SAMPLE_RATIO=1.0            # 0.0 to 1.0; 1.0 = sample all
OTLP_ENDPOINT=http://localhost:4317  # optional; omit to create spans without export
```

When enabled, framework auto-instrumentation is active for FastAPI and httpx. Custom workflow spans are created for run, step, and partition/job execution boundaries.

### Optional error monitoring

Sentry-compatible error monitoring can be enabled via `.env`:

```env
ERROR_MONITORING_ENABLED=true
ERROR_MONITORING_DSN=https://<key>@<org>.ingest.sentry.io/<project>
```

Sensitive data is scrubbed before events are transmitted (authorization headers, private keys, passwords, tokens). Correlation context (run_id, step_id, request_id) is attached to captured exceptions automatically.

---

## Running Tests

### Backend

```bash
cd backend
pytest          # all tests
pytest -v       # verbose output
pytest tests/test_csv_processor.py   # single file
pytest -k test_create_plan           # by name pattern
```

Tests use a file-based SQLite database (`backend/test_api.db`, cleaned up after the
run). No Salesforce connection is required.

**Against PostgreSQL:**

```bash
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/test_db \
  pytest -x -q
```

### Frontend

```bash
cd frontend
npm test            # watch mode
npm run test:run    # single run (CI)
npm run typecheck   # TypeScript type check
```
