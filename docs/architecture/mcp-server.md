# MCP server architecture

## What this covers / who should read this

How the Salesforce Bulk Loader's MCP server fits the system — what it is, how
it connects to the backend, how the two deployment channels work, and the
contracts governing discovery, tool binding, auth, and error handling. Read
this before extending the MCP server, adding tools, or wiring new deployment
channels.

---

## Overview

The MCP server is a **sidecar over the FastAPI REST API**. It exposes the
Bulk Loader's capabilities as MCP tools so that Claude Desktop (and other MCP
clients) can inspect plans, trigger loads, monitor job progress, and read error
results — without touching the browser UI.

The MCP server never bypasses the REST API. Every tool call ultimately becomes
an HTTP request to the FastAPI backend, which enforces all business logic,
access control, and observability instrumentation. The MCP server's job is to
translate MCP tool calls into REST requests and format the responses into
readable text.

```
Claude Desktop
  │  stdio (MCP protocol)
  ▼
sf_bulk_loader mcp        ← PyInstaller-bundled binary, "mcp" subcommand
  │
  │  HTTP  (localhost or HTTPS for hosted)
  ▼
FastAPI backend            ← all business logic lives here
  │
  ▼
SQLite / PostgreSQL
```

---

## Package layout

All MCP code lives under `mcp-server/src/sf_bulk_loader_mcp/`:

| Module | Role |
|---|---|
| `server.py` | Entrypoint — registers tools and starts the stdio server |
| `config.py` | `McpSettings` (pydantic-settings) — auth mode, base URL, PAT, app name |
| `client.py` | `BulkLoaderClient` — HTTP client with base URL + auth injection, HTTP→MCP error mapping |
| `discovery.py` | Desktop discovery — reads `mcp-discovery.json` from the OS data dir |
| `tools/health.py` | Hand-written `health` tool (`GET /api/health/ready`, excluded from OpenAPI schema) |
| `tools/connections.py` | Salesforce and storage (S3) connection tools |
| `tools/plans.py` | Load plan and step tools, SOQL validation, step preview |
| `tools/runs.py` | Load run and job tools including destructive actions |
| `tools/results.py` | Job result-file preview and logs-zip download |

The `CURATED_TOOLS` list in `server.py` is the authoritative index of all
implemented tools and the backend endpoint each wraps. See
[`mcp-server/docs/tool-binding.md`](../../mcp-server/docs/tool-binding.md) for
the full tool-binding contract.

---

## Two deployment channels

The channel is selected at MCP server startup via the `AUTH_MODE` environment
variable (default: `none`).

### Channel 1 — Desktop (AUTH_MODE=none)

Used when the Bulk Loader runs as an Electron desktop app.

- The MCP server binary is **bundled inside the desktop package** (via
  PyInstaller, assembled by SFBL-364). The binary entry point is the
  `sf_bulk_loader` binary with the `mcp` subcommand:
  `sf_bulk_loader mcp`.
- Claude Desktop's `claude_desktop_config.json` is updated automatically on
  macOS: on startup, `electron/main.js` calls `registerWithClaudeDesktop()`,
  which writes the binary path and `["mcp"]` args under the
  `mcpServers["sf-bulk-loader"]` key (idempotent; backs up the config file
  before writing).
- The backend's dynamic port is communicated to the MCP server via a
  **discovery file** — see the Discovery-file contract section below.
- No auth header is sent. The desktop profile runs with `APP_DISTRIBUTION=desktop`
  (`auth_mode=none`), so the backend accepts all requests from the loopback
  interface without credentials.

### Channel 2 — Hosted / PAT (AUTH_MODE=pat, SFBL-358 — deferred)

Intended for the self-hosted and AWS-hosted profiles. The MCP server runs
standalone (not bundled), pointed at a remote HTTPS endpoint. `BULKLOADER_BASE_URL`
is required; `BULKLOADER_PAT` carries the personal access token injected as
`Authorization: Bearer`. PAT authentication is shipped by SFBL-357 and the
hosted MCP channel is assembled by SFBL-358 — both deferred.

---

## Discovery-file contract

In the desktop channel the backend binds to a dynamic port (default start:
47000). Because the MCP server is a separate process with no Electron context,
port discovery uses a JSON file in the OS-convention user data directory.

**Writer:** `electron/main.js → writeDiscoveryFile()` — called after the
backend is confirmed healthy. Uses `buildDiscoveryPayload()` from
`electron/mcpRegistration.js`.

**Reader:** `mcp-server/src/sf_bulk_loader_mcp/discovery.py → read_discovery()`
— called lazily by `BulkLoaderClient` on first request when `AUTH_MODE=none`
and no `BULKLOADER_BASE_URL` override is present.

**File location** — must match Electron's `app.getPath('userData')`, which uses
`app.getName()` === `electron/package.json` `name` = `sf-bulk-loader-desktop`
(NOT the electron-builder `productName` "Salesforce Bulk Loader", which only
names the `.app`/`.dmg` artifact):

| Platform | Path |
|---|---|
| macOS | `~/Library/Application Support/sf-bulk-loader-desktop/mcp-discovery.json` |
| Linux | `$XDG_CONFIG_HOME/sf-bulk-loader-desktop/mcp-discovery.json` (or `~/.config/…`) |
| Windows | `%APPDATA%\sf-bulk-loader-desktop\mcp-discovery.json` |

**Schema** (validated by `discovery.py → DiscoveryFile`):

```json
{
  "schema_version": 1,
  "base_url": "http://127.0.0.1:<port>",
  "port": <int>,
  "pid": <int>
}
```

The `BULKLOADER_APP_NAME` env var overrides the `"Salesforce Bulk Loader"`
path segment — used in tests to point at a temp dir.

---

## Tool-binding contract

Tools are divided into two categories:

1. **Generated / curated tools** — built from schema-visible routes in
   `GET /openapi.json`. Auth headers and base URL are injected by `client.py`
   only; tool implementations call `client.get/post/put/delete` and never
   construct URLs or set auth headers directly.

2. **Hand-written tools** — endpoints tagged `include_in_schema=False` on the
   backend (currently only `health`). These cannot be generated from OpenAPI
   and must be kept in sync manually.

The full tool list, endpoint mapping, destructive-action safety rules, and
monitoring cadence are documented in
[`mcp-server/docs/tool-binding.md`](../../mcp-server/docs/tool-binding.md).

### Destructive-action safety

`trigger_run`, `abort_run`, and `retry_step` each carry two safety mechanisms:

- `ToolAnnotations(destructiveHint=True)` on the `Tool` definition.
- A required `confirm: true` boolean in their input schema — the handler calls
  `_runs.check_confirm(args)` before making any HTTP request and returns a
  structured refusal if `confirm` is absent or `False`.

### Error handling

All HTTP errors are mapped to `McpHttpError` in `client.py` before being
returned to the MCP caller. Raw stack traces are never surfaced. The safe
representation includes the HTTP status code, a human-readable class message,
and the backend `detail` field (FastAPI convention).

---

## Observability

The MCP server adds **no new run or job instrumentation**. All lifecycle events
(`RunEvent.STARTED`, `JobEvent.CREATED`, etc.) are already emitted by the
backend orchestrator via `app/observability/events.py`. Duplicating them in
the MCP layer would cause double-counting. See [`docs/observability.md`](../observability.md)
for the full event catalogue.

---

## Related

- [`mcp-server/docs/tool-binding.md`](../../mcp-server/docs/tool-binding.md) — tool-binding contract (curated tool list, endpoint map, safety rules)
- [`docs/architecture/mcp-tool-reference.md`](mcp-tool-reference.md) — grouped reference of all 36 tools
- [`docs/usage/using-the-mcp-server.md`](../usage/using-the-mcp-server.md) — operator guide (desktop MCP setup, headline loop)
- [`docs/deployment/desktop.md`](../deployment/desktop.md) — desktop deployment guide
- [`docs/observability.md`](../observability.md) — event taxonomy and metrics
