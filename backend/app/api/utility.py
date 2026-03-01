"""Utility API — file listing/preview, health check, and WebSocket run status."""

import asyncio
import csv
import logging
import os
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.utils.ws_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["utility"])
ws_router = APIRouter(tags=["websocket"])


# ── File endpoints ─────────────────────────────────────────────────────────────


def _safe_filename(filename: str) -> str:
    """Strip path separators to prevent directory traversal."""
    return os.path.basename(filename)


@router.get("/api/files/input", response_model=List[Dict[str, Any]])
async def list_input_files() -> List[Dict[str, Any]]:
    """List CSV files available in the input directory."""
    input_dir = settings.input_dir
    if not os.path.isdir(input_dir):
        return []
    files = []
    for name in sorted(os.listdir(input_dir)):
        if not name.lower().endswith(".csv"):
            continue
        full = os.path.join(input_dir, name)
        try:
            size = os.path.getsize(full)
        except OSError:
            size = 0
        files.append({"filename": name, "size_bytes": size})
    return files


@router.get("/api/files/input/{filename}/preview")
async def preview_input_file(
    filename: str,
    rows: int = Query(default=10, ge=1, le=1000),
) -> Dict[str, Any]:
    """Return the first *rows* data rows (plus header) of a CSV file."""
    safe_name = _safe_filename(filename)
    if safe_name != filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filename")

    filepath = os.path.join(settings.input_dir, safe_name)
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    try:
        with open(filepath, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            header = reader.fieldnames or []
            preview_rows = [row for _, row in zip(range(rows), reader)]
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not read file: {exc}",
        ) from exc

    return {
        "filename": safe_name,
        "header": list(header),
        "rows": preview_rows,
        "row_count": len(preview_rows),
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
async def websocket_run_status(websocket: WebSocket, run_id: str) -> None:
    """Stream real-time status events for an active load run.

    The client receives a ``connected`` event immediately on connection.
    The orchestrator pushes events (job_status_change, step_completed, …)
    via :func:`ws_manager.broadcast`.  A ``ping``/``pong`` keepalive runs
    every 30 s so proxies don't close idle connections.
    """
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
