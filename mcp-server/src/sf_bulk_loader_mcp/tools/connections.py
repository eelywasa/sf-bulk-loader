"""Connection tools — Salesforce (SF) and storage (S3 / input-connection) variants.

Implements SFBL-360: wrap the backend connection CRUD + test + list-objects
routes as MCP tools.

Input schemas are hand-written to match the backend request/response shapes
documented in ``backend/app/schemas/connection.py`` and
``backend/app/schemas/input_connection.py``.  The API already redacts
credentials in all responses; this module never echoes request secrets back
in tool output.

See docs/tool-binding.md for the full contract.
"""

from __future__ import annotations

from typing import Any

from ..client import BulkLoaderClient


# ── Salesforce connections ────────────────────────────────────────────────────


async def list_connections(client: BulkLoaderClient) -> list[dict[str, Any]]:
    """Call ``GET /api/connections`` and return the list payload."""
    response = await client.get("/api/connections/")
    return response.json()


def format_list_connections(payload: list[dict[str, Any]]) -> str:
    if not payload:
        return "No Salesforce connections found."
    lines = [f"Salesforce connections ({len(payload)}):"]
    for c in payload:
        sandbox = " [sandbox]" if c.get("is_sandbox") else ""
        lines.append(
            f"  id={c['id']}  name={c['name']!r}  instance_url={c['instance_url']!r}{sandbox}"
        )
    return "\n".join(lines)


async def get_connection(client: BulkLoaderClient, connection_id: str) -> dict[str, Any]:
    """Call ``GET /api/connections/{id}`` and return the connection payload."""
    response = await client.get(f"/api/connections/{connection_id}")
    return response.json()


def format_connection(payload: dict[str, Any]) -> str:
    sandbox = " [sandbox]" if payload.get("is_sandbox") else ""
    lines = [
        f"Connection: {payload['name']!r}{sandbox}",
        f"  id:           {payload['id']}",
        f"  instance_url: {payload.get('instance_url', 'N/A')}",
        f"  login_url:    {payload.get('login_url', 'N/A')}",
        f"  username:     {payload.get('username', 'N/A')}",
        f"  created_at:   {payload.get('created_at', 'N/A')}",
        f"  updated_at:   {payload.get('updated_at', 'N/A')}",
    ]
    if "client_id" in payload:
        lines.append(f"  client_id:    {payload['client_id']}")
    return "\n".join(lines)


async def create_connection(
    client: BulkLoaderClient, body: dict[str, Any]
) -> dict[str, Any]:
    """Call ``POST /api/connections`` and return the created connection payload."""
    response = await client.post("/api/connections/", json=body)
    return response.json()


def format_create_connection(payload: dict[str, Any]) -> str:
    return f"Connection created successfully.\n{format_connection(payload)}"


async def update_connection(
    client: BulkLoaderClient, connection_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Call ``PUT /api/connections/{id}`` and return the updated connection payload."""
    response = await client.put(f"/api/connections/{connection_id}", json=body)
    return response.json()


def format_update_connection(payload: dict[str, Any]) -> str:
    return f"Connection updated successfully.\n{format_connection(payload)}"


async def check_connection(
    client: BulkLoaderClient, connection_id: str
) -> dict[str, Any]:
    """Call ``POST /api/connections/{id}/test`` and return the test result."""
    response = await client.post(f"/api/connections/{connection_id}/test")
    return response.json()


def format_test_connection(payload: dict[str, Any]) -> str:
    success = payload.get("success", False)
    message = payload.get("message", "")
    instance_url = payload.get("instance_url")
    status_word = "PASSED" if success else "FAILED"
    parts = [f"Connection test {status_word}: {message}"]
    if instance_url:
        parts.append(f"  instance_url: {instance_url}")
    return "\n".join(parts)


async def list_sobjects(
    client: BulkLoaderClient, connection_id: str
) -> list[str]:
    """Call ``GET /api/connections/{id}/objects`` and return the SObject list."""
    response = await client.get(f"/api/connections/{connection_id}/objects")
    return response.json()


def format_list_sobjects(connection_id: str, payload: list[str]) -> str:
    if not payload:
        return f"No SObjects returned for connection {connection_id}."
    count = len(payload)
    sample = ", ".join(payload[:10])
    suffix = f" … and {count - 10} more" if count > 10 else ""
    return f"SObjects for connection {connection_id} ({count} total): {sample}{suffix}"


# ── Storage (input) connections ───────────────────────────────────────────────


async def list_input_connections(
    client: BulkLoaderClient,
    direction: str | None = None,
) -> list[dict[str, Any]]:
    """Call ``GET /api/input-connections`` and return the list payload."""
    params: dict[str, str] = {}
    if direction is not None:
        params["direction"] = direction
    response = await client.get("/api/input-connections/", params=params)
    return response.json()


def format_list_input_connections(payload: list[dict[str, Any]]) -> str:
    if not payload:
        return "No storage connections found."
    lines = [f"Storage connections ({len(payload)}):"]
    for ic in payload:
        lines.append(
            f"  id={ic['id']}  name={ic['name']!r}  provider={ic.get('provider', 'N/A')}"
            f"  bucket={ic.get('bucket', 'N/A')!r}  direction={ic.get('direction', 'N/A')}"
        )
    return "\n".join(lines)


async def get_input_connection(
    client: BulkLoaderClient, ic_id: str
) -> dict[str, Any]:
    """Call ``GET /api/input-connections/{id}`` and return the payload."""
    response = await client.get(f"/api/input-connections/{ic_id}")
    return response.json()


def format_input_connection(payload: dict[str, Any]) -> str:
    lines = [
        f"Storage connection: {payload['name']!r}",
        f"  id:          {payload['id']}",
        f"  provider:    {payload.get('provider', 'N/A')}",
        f"  bucket:      {payload.get('bucket', 'N/A')}",
        f"  root_prefix: {payload.get('root_prefix') or '(none)'}",
        f"  region:      {payload.get('region') or '(none)'}",
        f"  direction:   {payload.get('direction', 'N/A')}",
        f"  created_at:  {payload.get('created_at', 'N/A')}",
        f"  updated_at:  {payload.get('updated_at', 'N/A')}",
    ]
    return "\n".join(lines)


async def create_input_connection(
    client: BulkLoaderClient, body: dict[str, Any]
) -> dict[str, Any]:
    """Call ``POST /api/input-connections`` and return the created payload.

    Note: access_key_id, secret_access_key, and session_token are accepted in
    the request body but are NEVER echoed back; the API response omits them.
    """
    response = await client.post("/api/input-connections/", json=body)
    return response.json()


def format_create_input_connection(payload: dict[str, Any]) -> str:
    return f"Storage connection created successfully.\n{format_input_connection(payload)}"


async def update_input_connection(
    client: BulkLoaderClient, ic_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Call ``PUT /api/input-connections/{id}`` and return the updated payload."""
    response = await client.put(f"/api/input-connections/{ic_id}", json=body)
    return response.json()


def format_update_input_connection(payload: dict[str, Any]) -> str:
    return f"Storage connection updated successfully.\n{format_input_connection(payload)}"


async def check_input_connection(
    client: BulkLoaderClient, ic_id: str
) -> dict[str, Any]:
    """Call ``POST /api/input-connections/{id}/test`` and return the test result."""
    response = await client.post(f"/api/input-connections/{ic_id}/test")
    return response.json()


def format_test_input_connection(payload: dict[str, Any]) -> str:
    success = payload.get("success", False)
    message = payload.get("message", "")
    status_word = "PASSED" if success else "FAILED"
    return f"Storage connection test {status_word}: {message}"
