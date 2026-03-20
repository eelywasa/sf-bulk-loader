"""Utility API — file listing/preview, health check, and WebSocket run status."""

import asyncio
import logging
from enum import Enum
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
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
from app.utils.ws_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["utility"])
ws_router = APIRouter(tags=["websocket"])


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
    row_count: int
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
    rows: int = Query(default=10, ge=1, le=1000),
    source: Optional[str] = Query(default=None, description="Input source id or 'local'"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> InputPreviewResponse:
    """Return the first *rows* data rows (plus header) of a CSV file."""
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
        preview = storage.preview_file(file_path, rows)
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
        "row_count": preview.row_count,
        "source": source_id,
        "provider": provider,
    }


# ── Health check ───────────────────────────────────────────────────────────────


@router.get("/api/health")
async def health_check(db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """Return application health: DB connectivity and basic config."""
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
