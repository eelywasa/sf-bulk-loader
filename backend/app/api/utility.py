"""Utility API — file listing/preview, health check, and WebSocket run status."""

import asyncio
import json
import logging
from enum import Enum
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.services.auth import get_current_user, validate_ws_token
from app.services.input_storage import (
    BaseInputStorage,
    InputStorageError,
    InputConnectionNotFoundError,
    UnsupportedInputProviderError,
    get_storage,
)
from app.observability.events import OutcomeCode, SystemEvent
from app.observability.metrics import ws_active_connections
from app.utils.ws_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["utility"])
ws_router = APIRouter(tags=["websocket"])


# ── Metrics endpoint ────────────────────────────────────────────────────────────


@router.get("/metrics", include_in_schema=False)
async def metrics_endpoint():
    """Expose Prometheus-compatible metrics for scraping."""
    from fastapi.responses import Response
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


# ── File endpoints ─────────────────────────────────────────────────────────────


class EntryKind(str, Enum):
    file = "file"
    directory = "directory"


class InputDirectoryEntry(BaseModel):
    name: str
    kind: EntryKind
    path: str
    size_bytes: Optional[int] = None
    row_count: Optional[int] = None
    source: str
    provider: str


class InputPreviewResponse(BaseModel):
    filename: str
    header: list[str]
    rows: list[dict[str, Optional[str]]]
    total_rows: Optional[int]
    filtered_rows: Optional[int]
    offset: int
    limit: int
    has_next: bool
    source: str
    provider: str


def _resolve_provider(storage: BaseInputStorage) -> str:
    return storage.provider


@router.get("/api/files/input", response_model=List[InputDirectoryEntry])
async def list_input_files(
    path: str = Query(default="", description="Relative subdirectory path to list"),
    source: Optional[str] = Query(default=None, description="Input source id or 'local'"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> List[InputDirectoryEntry]:
    """List CSV files and subdirectories at the given path within the input directory."""
    try:
        storage = await get_storage(source, db)
    except InputConnectionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except UnsupportedInputProviderError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except InputStorageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    source_id = source or "local"
    provider = _resolve_provider(storage)
    try:
        entries = storage.list_entries(path)
    except InputStorageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return [
        InputDirectoryEntry(
            name=e.name,
            kind=EntryKind(e.kind),
            path=e.path,
            size_bytes=e.size_bytes,
            row_count=e.row_count,
            source=source_id,
            provider=provider,
        )
        for e in entries
    ]


@router.get("/api/files/input/{file_path:path}/preview", response_model=InputPreviewResponse)
async def preview_input_file(
    file_path: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    filters: Optional[str] = Query(default=None, description="JSON array of filter objects"),
    source: Optional[str] = Query(default=None, description="Input source id or 'local'"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> InputPreviewResponse:
    """Return a paginated page of data rows (plus header) from a CSV file."""
    try:
        storage = await get_storage(source, db)
    except InputConnectionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except UnsupportedInputProviderError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except InputStorageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # Parse filters JSON
    parsed_filters: list[dict[str, str]] | None = None
    if filters is not None:
        try:
            parsed = json.loads(filters)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid filters JSON: {exc}",
            ) from exc
        if not isinstance(parsed, list):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="filters must be a JSON array",
            )
        for item in parsed:
            if not isinstance(item, dict) or "column" not in item or "value" not in item:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Each filter must be an object with 'column' and 'value' keys",
                )
        parsed_filters = parsed

    source_id = source or "local"
    provider = _resolve_provider(storage)
    try:
        preview = await run_in_threadpool(storage.preview_file, file_path, limit, offset, parsed_filters)
    except InputStorageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not read file: {exc}",
        ) from exc

    return {
        "filename": preview.filename,
        "header": preview.header,
        "rows": preview.rows,
        "total_rows": preview.total_rows,
        "filtered_rows": preview.filtered_rows,
        "offset": preview.offset,
        "limit": preview.limit,
        "has_next": preview.has_next,
        "source": source_id,
        "provider": provider,
    }


# ── Runtime config ─────────────────────────────────────────────────────────────


class RuntimeConfigResponse(BaseModel):
    auth_mode: str
    app_distribution: str
    transport_mode: str
    input_storage_mode: str


@router.get("/api/runtime", response_model=RuntimeConfigResponse)
async def runtime_config() -> RuntimeConfigResponse:
    """Return the active distribution profile settings.

    Unauthenticated — the frontend calls this on startup to adapt its behaviour
    (e.g. whether to show the login screen).
    """
    return RuntimeConfigResponse(
        auth_mode=settings.auth_mode or "",
        app_distribution=settings.app_distribution,
        transport_mode=settings.transport_mode or "",
        input_storage_mode=settings.input_storage_mode or "",
    )


# ── Health check ───────────────────────────────────────────────────────────────


async def _check_database(db: AsyncSession) -> tuple[str, str | None]:
    """Run a lightweight DB ping. Returns (status, error_detail)."""
    try:
        await db.execute(text("SELECT 1"))
        return OutcomeCode.OK, None
    except Exception as exc:
        return OutcomeCode.FAILED, str(exc)


@router.get("/api/health/live", include_in_schema=False)
async def health_live() -> Dict[str, Any]:
    """Liveness probe: process is alive and can serve requests.

    Fast — no dependency checks. Fails only if the process itself is broken.
    """
    logger.debug("Liveness check", extra={"event_name": SystemEvent.HEALTH_CHECKED,
                                          "outcome_code": OutcomeCode.OK})
    return {"status": OutcomeCode.OK}


@router.get("/api/health/ready", include_in_schema=False)
async def health_ready(db: AsyncSession = Depends(get_db)) -> Any:
    """Readiness probe: service is ready to receive traffic.

    Checks database connectivity. Returns 503 if the database is unavailable.
    """
    from fastapi.responses import JSONResponse

    db_status, db_error = await _check_database(db)
    overall = OutcomeCode.OK if db_status == OutcomeCode.OK else OutcomeCode.FAILED
    logger.info(
        "Readiness check: %s",
        overall,
        extra={"event_name": SystemEvent.HEALTH_CHECKED, "outcome_code": overall},
    )
    payload: Dict[str, Any] = {"status": overall}
    if db_error:
        payload["database"] = db_error
    if overall != OutcomeCode.OK:
        return JSONResponse(status_code=503, content=payload)
    return payload


async def _check_email(backend_name: str) -> tuple[str, str | None]:
    """Probe the configured email backend.

    Returns (status, detail) where status is one of:
    - OutcomeCode.OK      — backend is healthy
    - OutcomeCode.DEGRADED — backend probe failed (email is non-critical)

    noop backend always returns OK without a network probe.
    SMTP and SES failures return DEGRADED, not FAILED, because email is
    not strictly required for app functionality.
    """
    if backend_name == "noop":
        return OutcomeCode.OK, "email backend is noop; no external probe performed"

    try:
        from app.services.email.service import _BACKENDS

        backend = _BACKENDS.get(backend_name)
        if backend is None:
            return OutcomeCode.DEGRADED, f"unknown email backend: {backend_name!r}"

        healthy = await asyncio.wait_for(backend.healthcheck(), timeout=3.0)
        if healthy:
            return OutcomeCode.OK, None
        return OutcomeCode.DEGRADED, f"{backend_name} healthcheck returned False"
    except asyncio.TimeoutError:
        return OutcomeCode.DEGRADED, f"{backend_name} healthcheck timed out after 3s"
    except Exception as exc:
        from app.observability.sanitization import safe_exc_message

        return OutcomeCode.DEGRADED, safe_exc_message(exc)


@router.get("/api/health/dependencies", include_in_schema=False)
async def health_dependencies(db: AsyncSession = Depends(get_db)) -> Any:
    """Dependency health: per-dependency status for operator inspection.

    Returns detailed view of each dependency with ok/degraded/failed status.
    """
    from fastapi.responses import JSONResponse

    deps: Dict[str, Any] = {}

    if settings.health_enable_dependency_checks:
        db_status, db_error = await _check_database(db)
        deps["database"] = {"status": db_status}
        if db_error:
            deps["database"]["detail"] = db_error

        # Email probe — always included; noop backend reports healthy without a network call
        email_status, email_detail = await _check_email(settings.email_backend or "noop")
        deps["email"] = {"status": email_status}
        if email_detail:
            deps["email"]["detail"] = email_detail
    else:
        deps["database"] = {"status": OutcomeCode.OK, "detail": "dependency checks disabled"}
        deps["email"] = {"status": OutcomeCode.OK, "detail": "dependency checks disabled"}

    # Overall: OK only if all deps are OK; DEGRADED if any are degraded;
    # FAILED if any are failed. Email degraded does not make the overall FAILED.
    statuses = {d.get("status") for d in deps.values()}
    if OutcomeCode.FAILED in statuses:
        overall = OutcomeCode.FAILED
    elif OutcomeCode.DEGRADED in statuses:
        overall = OutcomeCode.DEGRADED
    else:
        overall = OutcomeCode.OK

    logger.info(
        "Dependency health check: %s",
        overall,
        extra={"event_name": SystemEvent.HEALTH_CHECKED, "outcome_code": overall},
    )
    payload: Dict[str, Any] = {"status": overall, "dependencies": deps}
    if overall == OutcomeCode.FAILED:
        return JSONResponse(status_code=503, content=payload)
    return payload


@router.get("/api/health")
async def health_check(db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Return application health: DB connectivity and basic config.

    Kept for backward compatibility. Prefer /api/health/ready for readiness checks.
    """
    db_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = f"error: {exc}"

    overall = "ok" if db_status == "ok" else "degraded"
    return {
        "status": overall,
        "env": settings.app_env,
        "database": db_status,
        "sf_api_version": settings.sf_api_version,
    }


# ── WebSocket ──────────────────────────────────────────────────────────────────


@ws_router.websocket("/ws/runs/{run_id}")
async def websocket_run_status(
    websocket: WebSocket, run_id: str, token: Optional[str] = None
) -> None:
    """Stream real-time status events for an active load run.

    The client must supply a valid JWT via the ``token`` query parameter:
    ``/ws/runs/{run_id}?token=<jwt>``.  Connections without a valid token
    are rejected with close code 1008 (policy violation).

    After authentication, the client receives a ``connected`` event.
    The orchestrator pushes events (job_status_change, step_completed, …)
    via :func:`ws_manager.broadcast`.  A ``ping``/``pong`` keepalive runs
    every 30 s so proxies don't close idle connections.
    """
    try:
        validate_ws_token(token)
    except HTTPException:
        await websocket.close(code=1008)
        return

    await ws_manager.connect(run_id, websocket)
    try:
        await ws_manager.send_personal(websocket, {"event": "connected", "run_id": run_id})

        while True:
            try:
                # Wait for a client message (e.g. ping) with a 30-second timeout
                data = await asyncio.wait_for(websocket.receive_json(), timeout=30.0)
                if isinstance(data, dict) and data.get("type") == "ping":
                    await ws_manager.send_personal(websocket, {"type": "pong"})
            except asyncio.TimeoutError:
                # No message in 30 s — send a server-side keepalive ping
                await ws_manager.send_personal(websocket, {"type": "ping"})
    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected for run %s", run_id)
    except Exception as exc:
        logger.warning("WebSocket error for run %s: %s", run_id, exc)
    finally:
        ws_manager.disconnect(run_id, websocket)
