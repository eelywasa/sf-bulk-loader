"""SFBL-373: real PAT-authenticated MCP lifecycle integration test.

Unlike ``mcp-server/tests/test_pat_smoke.py`` — which mocks the HTTP transport
and only proves the MCP *client* attaches a Bearer header — this test drives the
**real backend FastAPI app** in hosted mode (``auth_mode='local'``) over an httpx
``ASGITransport``. It mints a Personal Access Token through the **real**
session-authenticated ``POST /api/me/tokens`` endpoint, then uses the MCP
``BulkLoaderClient`` in ``pat`` mode to exercise the
create-connection → create-plan → trigger-run → inspect lifecycle.

Every request therefore passes through the backend's genuine PAT verification
path (HMAC-SHA256 hash → DB lookup → ``selectinload(User.profile)`` → permission
gates), closing the assurance gap the mock smoke test left open. This is the
harness SFBL-373's QA round 2 specified: a hosted-mode backend, an operator-level
profile carrying ``tokens.manage``, a PAT minted via session auth, then the MCP
lifecycle driven with that PAT.

Cross-package note: this test lives under ``backend/tests/`` (not
``mcp-server/tests/``) because it needs BOTH the backend app and the
``sf_bulk_loader_mcp`` client importable in one process. CI installs the
mcp-server package into the backend test job (see ci-shared.yml).
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select

from app.main import app
from app.models.profile import Profile
from app.models.profile_permission import ProfilePermission
from app.models.user import User
from app.services.auth import create_access_token
from tests.conftest import _TestSession, _run_async

# The mcp-server package. Skip the whole module cleanly if it isn't installed
# (e.g. a local backend-only run that didn't `pip install ../mcp-server`).
pytest.importorskip(
    "sf_bulk_loader_mcp",
    reason="mcp-server package not installed; run `pip install ../mcp-server`",
)

from sf_bulk_loader_mcp.client import BulkLoaderClient, McpHttpError  # noqa: E402
from sf_bulk_loader_mcp.config import McpSettings  # noqa: E402
from sf_bulk_loader_mcp.tools import connections as conn_tools  # noqa: E402
from sf_bulk_loader_mcp.tools import plans as plan_tools  # noqa: E402
from sf_bulk_loader_mcp.tools import runs as run_tools  # noqa: E402


# The exact permission set an "operator who can mint PATs" needs to drive the
# full MCP lifecycle. Deliberately NOT the admin superset — this proves a
# least-privilege operator profile works end-to-end through real PAT auth.
_MCP_OPERATOR_PERMS = [
    "connections.view",
    "connections.manage",
    "plans.view",
    "plans.manage",
    "runs.view",
    "runs.execute",
    "tokens.manage",
]

_BASE_URL = "http://testserver"  # ASGITransport ignores the host; any value works


def _seed_operator_with_tokens_manage() -> User:
    """Create a persisted custom profile (operator perms + tokens.manage) and a
    user bound to it. Returns the user.

    The profile is persisted (real ``profile_permission`` rows) because the real
    PAT/JWT auth path re-loads ``User.profile`` from the DB — an in-memory profile
    would not satisfy the permission gates.
    """

    async def _do() -> User:
        async with _TestSession() as session:
            profile = Profile(
                name=f"mcp-operator-{uuid.uuid4().hex[:8]}",
                description="MCP integration test: operator + tokens.manage",
                is_system=False,
            )
            session.add(profile)
            await session.flush()
            for key in _MCP_OPERATOR_PERMS:
                session.add(
                    ProfilePermission(profile_id=profile.id, permission_key=key)
                )
            user = User(
                id=str(uuid.uuid4()),
                email=f"mcp-op-{uuid.uuid4().hex[:8]}@example.com",
                hashed_password="x",
                status="active",
                is_admin=False,  # operator-level, not admin
                profile_id=profile.id,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    return _run_async(_do())


def _mint_pat_via_session_endpoint(client, user: User, name: str = "mcp-integration") -> tuple[str, str]:
    """Mint a PAT through the REAL ``POST /api/me/tokens`` endpoint using session
    (JWT) auth. Returns (token_id, plaintext_token).

    This exercises ``require_session_auth`` (rejects PAT-auth) and the inline
    ``tokens.manage`` permission gate — exactly the production minting path.
    """
    jwt = create_access_token(user)
    resp = client.post(
        "/api/me/tokens",
        json={"name": name},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert resp.status_code == 201, (
        f"PAT mint failed: {resp.status_code} {resp.text}. "
        "Expected the session-authenticated operator to mint a PAT."
    )
    body = resp.json()
    token = body["token"]
    assert token.startswith("sfbl_pat_"), f"Unexpected token prefix: {token[:12]}"
    return body["id"], token


def _mcp_settings(pat: str) -> McpSettings:
    return McpSettings(
        auth_mode="pat",
        bulkloader_base_url=_BASE_URL,
        bulkloader_pat=pat,
    )


def _new_mcp_client(pat: str) -> BulkLoaderClient:
    """A BulkLoaderClient in PAT mode wired to the real app via ASGITransport."""
    bl_client = BulkLoaderClient(_mcp_settings(pat))
    bl_client._http = httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url=_BASE_URL
    )
    return bl_client


_CONNECTION_BODY = {
    "name": "MCP integration org",
    "instance_url": "https://mcp-int.my.salesforce.com",
    "login_url": "https://login.salesforce.com",
    "client_id": "mcpIntegrationClientId",
    # Any non-empty string — encrypted at rest, never validated against a live
    # org at create time (only POST /connections/{id}/test contacts Salesforce).
    "private_key": "-----BEGIN PRIVATE KEY-----\nFAKEKEYMATERIAL\n-----END PRIVATE KEY-----",
    "username": "mcp-integration@example.com",
}


class TestMcpPatLifecycleAgainstRealBackend:
    """The MCP client in PAT mode drives the real backend through real PAT auth."""

    def test_pat_authenticated_lifecycle_create_run_inspect(self, client):
        """create-connection → create-plan → trigger-run → inspect, all via a
        real minted PAT against the real backend.

        Uses the ``client`` fixture so get_db points at the test session and the
        orchestrator is no-op'd (the run stays ``pending`` instead of attempting
        a real Salesforce call in the background task).
        """
        user = _seed_operator_with_tokens_manage()
        _, pat = _mint_pat_via_session_endpoint(client, user)

        async def _drive() -> dict:
            bl = _new_mcp_client(pat)
            async with bl:
                # 1. Create a Salesforce connection (connections.manage)
                conn = await conn_tools.create_connection(bl, dict(_CONNECTION_BODY))
                assert conn.get("id"), f"No connection id in {conn}"

                # 2. Create a load plan (plans.manage)
                plan = await plan_tools.create_plan(
                    bl,
                    {"connection_id": conn["id"], "name": "MCP integration plan"},
                )
                assert plan.get("id"), f"No plan id in {plan}"

                # 3. Trigger a run (runs.execute)
                run = await run_tools.trigger_run(bl, plan["id"])
                assert run.get("id"), f"No run id in {run}"

                # 4. Inspect the run (runs.view)
                detail = await run_tools.get_run(bl, run["id"])
                return detail

        detail = _run_async(_drive())
        # Orchestrator is no-op'd, so the run is created but not executed.
        assert detail["status"] == "pending", (
            f"Expected pending run (orchestrator no-op'd), got {detail.get('status')}"
        )

    def test_invalid_pat_rejected_by_real_backend(self, client):
        """A structurally-valid but unknown PAT is rejected by the real backend's
        hash lookup with a 401 — proving real verification, not a mock."""
        bogus = "sfbl_pat_" + "x" * 43

        async def _drive() -> None:
            bl = _new_mcp_client(bogus)
            async with bl:
                await conn_tools.list_connections(bl)

        with pytest.raises(McpHttpError) as exc_info:
            _run_async(_drive())
        assert exc_info.value.status_code == 401

    def test_revoked_pat_rejected_by_real_backend(self, client):
        """After the PAT is revoked via the real DELETE endpoint, the MCP client
        is rejected with 401 — exercising the backend's revocation check, which a
        mock transport cannot prove."""
        user = _seed_operator_with_tokens_manage()
        token_id, pat = _mint_pat_via_session_endpoint(client, user)

        # Sanity: the PAT works before revocation.
        async def _list() -> None:
            bl = _new_mcp_client(pat)
            async with bl:
                await conn_tools.list_connections(bl)

        _run_async(_list())  # no raise

        # Revoke via the real session-authenticated endpoint.
        jwt = create_access_token(user)
        del_resp = client.delete(
            f"/api/me/tokens/{token_id}",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert del_resp.status_code == 204, f"Revoke failed: {del_resp.status_code}"

        # The same PAT must now be rejected.
        with pytest.raises(McpHttpError) as exc_info:
            _run_async(_list())
        assert exc_info.value.status_code == 401
