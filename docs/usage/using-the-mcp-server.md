---
title: Using the MCP server
slug: using-the-mcp-server
nav_order: 95
tags: [mcp, automation, claude, desktop]
summary: >-
  Connect Claude Desktop to the Bulk Loader's MCP server to trigger loads,
  monitor progress, and diagnose failures without leaving your AI chat.
---

# Using the MCP server

## What this covers / who should read this

This page is for **desktop users** who want to drive the Salesforce Bulk Loader
from Claude Desktop using the Model Context Protocol (MCP). You will learn what
the desktop MCP server is, how it is registered automatically on macOS, and how
to use the core "trigger a load and ask why it failed" workflow. The MCP server
works with the desktop distribution profile only — hosted-profile support is a
future enhancement.

---

## What is the MCP server?

The Bulk Loader desktop app bundles a small Python MCP server alongside its
backend. When you open the app on macOS, it:

1. Starts the FastAPI backend on a local port.
2. Writes a discovery file (`mcp-discovery.json`) so the MCP server can find
   that port.
3. Registers the MCP server entry in Claude Desktop's
   `~/Library/Application Support/Claude/claude_desktop_config.json`.

The next time you open Claude Desktop, a new `sf-bulk-loader` tool group
appears. You can ask Claude to list your load plans, trigger a run, watch it
complete, and explain any failures — all without opening the browser UI.

---

## Auto-registration on macOS

Registration is automatic and idempotent. Every time the app starts:

- The `sf-bulk-loader` key under `mcpServers` in `claude_desktop_config.json`
  is created or overwritten with the current binary path.
- Sibling MCP server entries are preserved unchanged.
- A backup of the config file is saved as `claude_desktop_config.json.bak`
  before any write.

You do not need to configure anything manually. After the first launch, restart
Claude Desktop once to pick up the new entry.

> **Uninstalling.** The app does not yet auto-remove the config entry on
> uninstall. If you uninstall the app, remove the `"sf-bulk-loader"` key from
> `~/Library/Application Support/Claude/claude_desktop_config.json` manually.

---

## The "trigger a load and ask why it failed" loop

The headline workflow is:

1. **Ask Claude to list your plans** — `list_plans` returns all plans with
   their IDs and step counts.
2. **Trigger a run** — `trigger_run(plan_id=<uuid>, confirm=true)` starts a
   new `LoadRun` and returns its ID. You must pass `confirm=true` because
   triggering a run executes real Salesforce Bulk API DML.
3. **Poll for completion** — ask Claude to check `get_run(run_id=<uuid>)` every
   few seconds until the status is `completed`, `completed_with_errors`,
   `failed`, or `aborted`. Claude will apply exponential backoff automatically.
4. **Inspect failures** — if the run ended with errors, `failure_summary` reads
   the error CSVs for the failed jobs and returns the top Salesforce error
   reasons by frequency. Ask "why did this run fail?" and Claude will call
   `failure_summary` and explain the error patterns.
5. **Retry failed records** — `retry_step(run_id=<uuid>, step_id=<uuid>,
   confirm=true)` creates a new run that retries only the failed rows from that
   step.

---

## Available tools at a glance

The MCP server exposes 36 tools grouped into five areas:

| Area | What you can do |
|---|---|
| **Health** | Check that the backend is ready (`health`) |
| **Connections** | List, create, update, test Salesforce org connections and S3 storage connections |
| **Plans and steps** | List, create, update, delete plans; add/update/delete/reorder steps; validate SOQL; preview step input |
| **Runs and jobs** | Trigger, list, abort runs; list and inspect individual job partitions |
| **Results** | Preview success/error/unprocessed CSV rows; aggregate failure reasons; download logs ZIP |

The three tools that mutate live Salesforce data — `trigger_run`, `abort_run`,
and `retry_step` — require `confirm=true` and carry a destructive-action hint
so Claude will confirm before proceeding.

For the full tool list including endpoint mapping see
[`docs/architecture/mcp-tool-reference.md`](../architecture/mcp-tool-reference.md).

---

## Monitoring a run

The MCP channel uses REST polling — there is no WebSocket support. When you ask
Claude to monitor a run, it calls `get_run` on a polling loop:

- Initial interval: 5 s (matches the backend's own Bulk API polling interval).
- Backoff: doubles each cycle, capped at 60 s.
- Hard timeout: 30 minutes for most loads; increase this for very large plans.
- Terminal statuses: `completed`, `completed_with_errors`, `failed`, `aborted`.

For per-partition detail during a run, use `list_jobs(run_id=<uuid>)`.

---

## Limitations

- **Desktop profile only.** The MCP server runs without authentication. It
  cannot be used with self-hosted or AWS-hosted deployments — that requires a
  Personal Access Token channel that is not yet shipped.
- **macOS auto-registration only.** Linux and Windows users can manually add
  the `sf-bulk-loader` entry to their Claude Desktop config if needed; see the
  binary path in `~/Library/Application Support/Salesforce Bulk Loader/mcp-discovery.json`.
- **No WebSocket.** Real-time run updates from the WebSocket at
  `/ws/runs/{run_id}` are not available in the MCP channel. Use polling via
  `get_run`.
- **Result CSVs are previewed, not downloaded inline.** `download_logs_zip`
  saves the ZIP to disk and returns the path; it cannot stream binary content
  into the conversation.

---

## Related

- [Running a load](running-loads.md) — UI-based equivalent of the MCP workflow
- [Authoring load plans](load-plans.md) — set up the plan before triggering it
- Architecture: [MCP server overview](../architecture/mcp-server.md)
- Architecture: [MCP tool reference](../architecture/mcp-tool-reference.md)
