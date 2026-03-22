# Desktop Deployment

> **Status: Planned (Tickets 7–8)**
>
> The Electron desktop distribution is not yet implemented. This document describes
> the intended configuration model. See
> [docs/specs/distrubution-layer-spec.md](../specs/distrubution-layer-spec.md)
> for the full design.

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

## Workspace Layout (planned)

```
~/Library/Application Support/SalesforceBulkLoader/   (macOS)
%APPDATA%\SalesforceBulkLoader\                        (Windows)
~/.config/SalesforceBulkLoader/                        (Linux)
├── bulk_loader.db    # SQLite database
├── logs/             # Application logs
├── input/            # Source CSV files
└── output/           # Result files
```

Packaged application assets are kept separate from runtime data and user workspace.
