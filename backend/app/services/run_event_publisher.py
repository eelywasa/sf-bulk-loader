"""Typed WebSocket event publishers for run/step/job lifecycle events.

All ``ws_manager.broadcast`` calls are centralised here so the rest of the
execution engine never touches the WebSocket layer directly.
"""

from __future__ import annotations

from app.utils.ws_manager import ws_manager


async def publish_run_started(run_id: str) -> None:
    await ws_manager.broadcast(run_id, {"event": "run_started", "run_id": run_id})


async def publish_run_failed(run_id: str, *, error: str) -> None:
    await ws_manager.broadcast(
        run_id, {"event": "run_failed", "run_id": run_id, "error": error}
    )


async def publish_run_aborted(run_id: str, *, reason: str | None = None) -> None:
    payload: dict = {"event": "run_aborted", "run_id": run_id}
    if reason is not None:
        payload["reason"] = reason
    await ws_manager.broadcast(run_id, payload)


async def publish_run_completed(
    run_id: str,
    *,
    status: str,
    total_records: int,
    total_success: int,
    total_errors: int,
) -> None:
    await ws_manager.broadcast(
        run_id,
        {
            "event": "run_completed",
            "run_id": run_id,
            "status": status,
            "total_records": total_records,
            "total_success": total_success,
            "total_errors": total_errors,
        },
    )


async def publish_step_started(
    run_id: str,
    *,
    step_id: str,
    object_name: str,
    sequence: int,
) -> None:
    await ws_manager.broadcast(
        run_id,
        {
            "event": "step_started",
            "run_id": run_id,
            "step_id": step_id,
            "object_name": object_name,
            "sequence": sequence,
        },
    )


async def publish_step_completed(
    run_id: str,
    *,
    step_id: str,
    object_name: str,
    records_processed: int,
    records_success: int,
    records_failed: int,
    step_failed: bool,
) -> None:
    await ws_manager.broadcast(
        run_id,
        {
            "event": "step_completed",
            "run_id": run_id,
            "step_id": step_id,
            "object_name": object_name,
            "records_processed": records_processed,
            "records_success": records_success,
            "records_failed": records_failed,
            "step_failed": step_failed,
        },
    )


async def publish_job_status_change(
    run_id: str,
    *,
    step_id: str,
    job_id: str,
    status: str,
    sf_job_id: str | None = None,
    records_processed: int | None = None,
    records_failed: int | None = None,
    total_records: int | None = None,
) -> None:
    payload: dict = {
        "event": "job_status_change",
        "run_id": run_id,
        "step_id": step_id,
        "job_id": job_id,
        "status": status,
    }
    if sf_job_id is not None:
        payload["sf_job_id"] = sf_job_id
    if records_processed is not None:
        payload["records_processed"] = records_processed
    if records_failed is not None:
        payload["records_failed"] = records_failed
    if total_records is not None:
        payload["total_records"] = total_records
    await ws_manager.broadcast(run_id, payload)
