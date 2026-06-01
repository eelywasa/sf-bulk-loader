# sf-bulk-loader-mcp

MCP (Model Context Protocol) server for the Salesforce Bulk Loader.  Exposes
lifecycle tools (connections, plans, runs, job results) to Claude and other MCP
clients over stdio transport.

## Quick start (local dev)

### Prerequisites

- Python 3.12+
- A running Salesforce Bulk Loader backend (`uvicorn app.main:app --reload` in
  `backend/`, or `docker compose up`).

### Install

```bash
cd mcp-server
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Run against a local backend

In desktop/none mode the server discovers the backend URL from the Electron
discovery file written by `SFBL-364`.  For local dev you can skip discovery
entirely by setting the explicit override:

```bash
export BULKLOADER_BASE_URL=http://localhost:8000
python3 -m sf_bulk_loader_mcp.server
# or via the console entrypoint:
sf-bulk-loader-mcp
```

The server listens on **stdio** and speaks the MCP protocol.  Connect Claude
Desktop or any MCP client to it using the `sf-bulk-loader-mcp` command.

### Environment variables

| Variable              | Default                   | Description                                               |
|-----------------------|---------------------------|-----------------------------------------------------------|
| `AUTH_MODE`           | `none`                    | `none` (desktop) or `pat` (remote/hosted, SFBL-371).      |
| `BULKLOADER_BASE_URL` | _(discovery file)_        | Override the backend URL.  **Always wins** for local dev. |
| `BULKLOADER_PAT`      | —                         | Personal Access Token.  Required when `AUTH_MODE=pat`.    |
| `BULKLOADER_APP_NAME` | `Salesforce Bulk Loader`  | Electron `productName` used to locate the data dir.       |

### Desktop mode (no env vars needed after SFBL-364)

When the Salesforce Bulk Loader desktop app is running, it writes
`mcp-discovery.json` to the OS data directory:

| OS      | Path                                                              |
|---------|-------------------------------------------------------------------|
| macOS   | `~/Library/Application Support/Salesforce Bulk Loader/mcp-discovery.json` |
| Linux   | `$XDG_CONFIG_HOME/Salesforce Bulk Loader/mcp-discovery.json` (or `~/.config/…`) |
| Windows | `%APPDATA%\Salesforce Bulk Loader\mcp-discovery.json`            |

The MCP server reads this file to find the backend port on startup.

### PAT / hosted mode

```bash
export AUTH_MODE=pat
export BULKLOADER_BASE_URL=https://your-hosted-instance.example.com
export BULKLOADER_PAT=<your-token>
sf-bulk-loader-mcp
```

Full PAT rotation and auth wiring ships in SFBL-371.

## Running tests

```bash
cd mcp-server
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
pytest -q
```

## Tool reference

See [docs/tool-binding.md](docs/tool-binding.md) for the full tool-binding
contract, the curated tool list, and the error-handling convention.
