# Desktop Deployment

> **Status: Implemented (Tickets 7–8)**

---

## Profile

The desktop distribution uses the `desktop` profile:

```
APP_DISTRIBUTION=desktop
```

This enforces:

| Setting | Value | Notes |
|---------|-------|-------|
| `auth_mode` | `none` | No login required — single-user local tool |
| `transport_mode` | `local` | Loopback only; backend must not bind to network interfaces |
| `input_storage_mode` | `local` | Local filesystem |
| `DATABASE_URL` | SQLite only | PostgreSQL is rejected at startup for this profile |

## Authentication

The desktop profile requires **no login**. This is a deliberate distribution policy for a
single-user local tool, not an accidental bypass. The `auth_mode=none` setting causes the
backend to return a synthetic user for all requests, and the frontend skips the login screen
entirely.

There is no user database seeded on desktop startup. The `ADMIN_USERNAME` and
`ADMIN_PASSWORD` bootstrap credentials are ignored.

## Transport

The desktop profile uses `transport_mode=local`. The backend is intended to be bound to
`127.0.0.1` only and must not be accessible from other machines on the network. This binding
is enforced by the Electron launcher (Ticket 7). Plain HTTP and WebSocket (`ws://`) over
loopback are acceptable — no TLS is required for local loopback communication.

---

## Intended Packaging

- Electron shell wrapping the React frontend
- Bundled FastAPI backend process managed by Electron's main process
- Backend bound to `127.0.0.1` only (not network-accessible)
- No nginx in the desktop distribution
- SQLite database stored in the user's application data directory

## Secrets

For the MVP, secrets (Salesforce private keys) are stored encrypted in the application
database using the same Fernet encryption model as the hosted distributions.

OS-native secure storage (macOS Keychain, Windows Credential Manager) is an explicitly
planned future enhancement — it is not forgotten, simply deferred past MVP.

## Workspace Layout

```
~/Library/Application Support/SalesforceBulkLoader/   (macOS)
%APPDATA%\SalesforceBulkLoader\                        (Windows)
~/.config/SalesforceBulkLoader/                        (Linux)
├── db/
│   ├── bulk_loader.db    # SQLite database
│   ├── encryption.key    # Auto-generated Fernet key (mode 0o600)
│   └── jwt_secret.key    # Auto-generated JWT signing secret (mode 0o600)
├── logs/                 # Application logs
├── input/                # Source CSV files
└── output/               # Result files
```

The `db/` subdirectory mirrors Docker's `/data/db/` layout. Packaged application assets
are kept separate from runtime data and user workspace.

## First Launch and Database Migrations

On first launch, Electron runs `alembic upgrade head` synchronously before starting the
backend. This creates the full database schema in `db/bulk_loader.db`. On subsequent
launches the same command runs again as a no-op, or applies any pending migrations if the
app has been updated. No user action is required — migrations are fully automatic.

---

## Development Workflow

This section covers running the Electron skeleton from source during development. A packaged
installer is a future step — see Ticket 8.

### Prerequisites

- Node.js 20+
- Python 3.12+ with a backend venv at `backend/.venv` (see `docs/development.md`)

```bash
# Create backend venv if not already done
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Build the frontend for desktop

```bash
cd electron
npm install           # install Electron (first time only)
npm run build:frontend
```

This runs `VITE_API_URL=http://127.0.0.1:8000 vite build --base ./` in the `frontend/`
directory. The built output lands at `frontend/dist/`. The `--base ./` flag makes asset
paths relative so Electron can load them from the filesystem. `VITE_API_URL` is baked in
at build time so the frontend knows where to reach the backend.

### Launch

```bash
cd electron
npm start
```

Electron will:
1. Locate the `alembic` binary (`backend/.venv/bin/alembic`, or system `alembic`)
2. Run `alembic upgrade head` to initialise or migrate the database
3. Locate the `uvicorn` binary (`backend/.venv/bin/uvicorn`, or system `uvicorn`)
4. Spawn `uvicorn` bound to `127.0.0.1:8000` with `APP_DISTRIBUTION=desktop`
5. Poll `/api/health` until the backend is ready (up to 30 seconds)
6. Open the application window loading `frontend/dist/index.html`

The login screen is skipped — `desktop` profile sets `auth_mode=none`.

### Data directory

Runtime data is stored in the OS user-data directory:

| Platform | Path |
|----------|------|
| macOS | `~/Library/Application Support/sf-bulk-loader-desktop/` |
| Windows | `%APPDATA%\sf-bulk-loader-desktop\` |
| Linux | `~/.config/sf-bulk-loader-desktop/` |

Contents:

```
<data-dir>/
├── db/
│   ├── bulk_loader.db    # SQLite database
│   ├── encryption.key    # Auto-generated Fernet key
│   └── jwt_secret.key    # Auto-generated JWT signing secret
├── input/                # Source CSV files
├── output/               # Result CSVs
└── logs/
```

### Note on `webSecurity: false`

The Electron window runs with `webSecurity: false`. This allows the `file://`-loaded
frontend to make HTTP requests to `http://127.0.0.1:8000` without CORS blocking. This is
acceptable because:

- The backend binds to loopback only (`127.0.0.1`) and is not network-accessible
- The desktop profile uses `auth_mode=none` — there are no credentials to intercept
- No cross-origin data leaves the local machine

### Secrets model

Encryption and JWT keys are auto-generated on first launch and stored in `<data-dir>/db/`
with mode `0o600`. Salesforce private keys are Fernet-encrypted at rest in the SQLite database.

OS-native secure storage (macOS Keychain, Windows Credential Manager, Linux libsecret) is an
explicitly planned future enhancement — deferred past MVP.
