"""Run lifecycle coordinator: entry points, run-level state transitions.

Owns the top-level orchestration flow for both normal runs and retry runs.
Collaborates with :mod:`step_executor` for per-step work and
:mod:`run_event_publisher` for WebSocket events.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models.job import JobRecord, JobStatus
from app.models.load_plan import LoadPlan
from app.models.load_run import LoadRun, RunStatus
from app.models.load_step import LoadStep
from app.services import step_executor as _step_executor_mod
from app.services.csv_processor import partition_csv as _default_partition
from app.services.input_storage import InputStorageError, get_storage as _default_get_storage
from app.services.partition_executor import process_partition as _default_process
from app.services.result_persistence import count_csv_rows
from app.services.run_event_publisher import (
    publish_run_aborted,
    publish_run_completed,
    publish_run_failed,
    publish_run_started,
    publish_step_completed,
    publish_step_started,
)
from app.observability.context import (
    input_connection_id_ctx_var,
    load_plan_id_ctx_var,
    run_id_ctx_var,
    step_id_ctx_var,
)
from app.observability.error_monitoring import capture_exception
from app.observability.events import OutcomeCode, RunEvent, StepEvent
from app.observability.metrics import (
    record_run_completed,
    record_run_preflight_failure,
    record_run_started,
)
from app.observability import tracing
from app.services.salesforce_auth import get_access_token as _default_get_token
from app.services.salesforce_bulk import BulkAPIError, SalesforceBulkClient

logger = logging.getLogger(__name__)

_DbFactory = Callable[[], AsyncSession]


async def execute_run(run_id: str) -> None:
    """Background entry point: run a LoadRun end-to-end."""
    from opentelemetry import trace as otel_trace
    from opentelemetry.trace import NonRecordingSpan

    tracer = tracing._get_tracer()
    with tracer.start_as_current_span("run.execute") as span:
        span.set_attribute("run.id", run_id)
        try:
            async with AsyncSessionLocal() as db:
                await _execute_run(run_id, db, db_factory=AsyncSessionLocal)
        except Exception as exc:
            logger.exception(
                "execute_run: unhandled exception for run %s",
                run_id,
                extra={"run_id": run_id, "outcome_code": OutcomeCode.UNEXPECTED_EXCEPTION},
            )
            if not isinstance(span, NonRecordingSpan):
                span.record_exception(exc)
            capture_exception(exc, outcome_code=OutcomeCode.UNEXPECTED_EXCEPTION)


async def execute_retry_run(
    run_id: str,
    step_id: str,
    partitions: list[bytes],
    *,
    _get_token: Callable = _default_get_token,
    _BulkClient: type = SalesforceBulkClient,
) -> None:
    """Background entry point: execute a retry run for a single step.

    Processes the supplied pre-built CSV *partitions* rather than re-globbing
    the original CSV files.
    """
    async with AsyncSessionLocal() as db:
        # ── Load run + plan + connection ──────────────────────────────────────
        result = await db.execute(
            select(LoadRun)
            .where(LoadRun.id == run_id)
            .options(
                selectinload(LoadRun.load_plan).selectinload(LoadPlan.connection),
            )
        )
        run = result.scalar_one_or_none()
        if run is None:
            logger.error("execute_retry_run: LoadRun %s not found", run_id,
                         extra={"run_id": run_id})
            return

        step_result = await db.execute(select(LoadStep).where(LoadStep.id == step_id))
        step = step_result.scalar_one_or_none()
        if step is None:
            logger.error("execute_retry_run: LoadStep %s not found", step_id,
                         extra={"run_id": run_id, "step_id": step_id})
            await _mark_run_failed(run_id, db)
            return

        plan: LoadPlan = run.load_plan

        # Bind workflow context for this background task.
        run_id_ctx_var.set(run_id)
        step_id_ctx_var.set(step_id)
        load_plan_id_ctx_var.set(str(plan.id))

        # ── Mark run as running ───────────────────────────────────────────────
        run.status = RunStatus.running
        run.started_at = datetime.now(timezone.utc)
        await db.commit()

        await publish_run_started(run_id)
        logger.info(
            "Retry run %s started: step=%s partitions=%d",
            run_id,
            step_id,
            len(partitions),
            extra={"event_name": RunEvent.STARTED, "run_id": run_id,
                   "step_id": step_id, "load_plan_id": str(plan.id)},
        )

        # ── Obtain Salesforce access token ────────────────────────────────────
        try:
            access_token = await _get_token(db, plan.connection)
        except Exception as exc:
            logger.error("Retry run %s: failed to obtain access token: %s", run_id, exc,
                         extra={"event_name": RunEvent.FAILED,
                                "outcome_code": OutcomeCode.AUTH_ERROR,
                                "run_id": run_id})
            await _mark_run_failed(run_id, db, error_summary={"auth_error": str(exc)})
            await publish_run_failed(run_id, error=str(exc))
            return

        semaphore = asyncio.Semaphore(plan.max_parallel_jobs)

        # ── Create JobRecord rows ─────────────────────────────────────────────
        job_record_ids = await _build_retry_job_records(db, run_id, step_id, partitions)

        # ── Process partitions concurrently ───────────────────────────────────
        async with _BulkClient(plan.connection.instance_url, access_token) as bulk_client:
            tasks = [
                _default_process(
                    run_id=run_id,
                    step=step,
                    job_record_id=jr_id,
                    csv_data=csv_data,
                    bulk_client=bulk_client,
                    semaphore=semaphore,
                    db_factory=AsyncSessionLocal,
                )
                for jr_id, csv_data in zip(job_record_ids, partitions)
            ]
            gather_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Transition any stuck intermediate-state jobs to failed
        await db.execute(
            update(JobRecord)
            .where(
                JobRecord.id.in_(job_record_ids),
                JobRecord.status.in_([
                    JobStatus.pending,
                    JobStatus.uploading,
                    JobStatus.upload_complete,
                    JobStatus.in_progress,
                ]),
            )
            .values(status=JobStatus.failed, completed_at=datetime.now(timezone.utc))
        )
        await db.commit()

        # ── Aggregate results ─────────────────────────────────────────────────
        total_success = 0
        total_errors = 0
        for i, res in enumerate(gather_results):
            if isinstance(res, Exception):
                logger.error(
                    "Retry run %s partition %d: unhandled exception: %s",
                    run_id,
                    i,
                    res,
                    extra={"run_id": run_id},
                )
            elif isinstance(res, tuple):
                success, errors = res
                total_success += success
                total_errors += errors

        total_records = total_success + total_errors
        final_status = (
            RunStatus.completed_with_errors if total_errors > 0 else RunStatus.completed
        )

        run.status = final_status
        run.completed_at = datetime.now(timezone.utc)
        run.total_records = total_records
        run.total_success = total_success
        run.total_errors = total_errors
        await db.commit()

        await publish_run_completed(
            run_id,
            status=final_status.value,
            total_records=total_records,
            total_success=total_success,
            total_errors=total_errors,
        )
        retry_outcome = OutcomeCode.DEGRADED if total_errors > 0 else OutcomeCode.OK
        logger.info(
            "Retry run %s completed (%s): records=%d success=%d errors=%d",
            run_id,
            final_status.value,
            total_records,
            total_success,
            total_errors,
            extra={"event_name": RunEvent.COMPLETED, "outcome_code": retry_outcome,
                   "run_id": run_id, "load_plan_id": str(plan.id)},
        )


async def _build_retry_job_records(
    db: AsyncSession,
    run_id: str,
    step_id: str,
    partitions: list[bytes],
) -> list[str]:
    """Create ``JobRecord`` rows for retry partitions.

    Isolates retry-partition construction from retry-run submission.

    Returns:
        List of ``JobRecord`` IDs in partition order.
    """
    job_records: list[JobRecord] = []
    for idx, csv_data in enumerate(partitions):
        jr = JobRecord(
            load_run_id=run_id,
            load_step_id=step_id,
            partition_index=idx,
            status=JobStatus.pending,
            total_records=count_csv_rows(csv_data),
        )
        db.add(jr)
        job_records.append(jr)
    await db.commit()
    for jr in job_records:
        await db.refresh(jr)
    return [jr.id for jr in job_records]


async def _execute_run(
    run_id: str,
    db: AsyncSession,
    *,
    db_factory: _DbFactory = AsyncSessionLocal,
    _get_token: Callable = _default_get_token,
    _BulkClient: type = SalesforceBulkClient,
    _get_storage: Callable = _default_get_storage,
    _partition: Callable = _default_partition,
) -> None:
    """Orchestrate a load run.

    Injectable parameters allow the orchestrator facade to pass patched
    bindings so that existing ``patch("app.services.orchestrator.X")`` calls
    in tests continue to work.
    """
    # ── Load run + plan + steps + connection ──────────────────────────────────
    result = await db.execute(
        select(LoadRun)
        .where(LoadRun.id == run_id)
        .options(
            selectinload(LoadRun.load_plan).selectinload(LoadPlan.load_steps),
            selectinload(LoadRun.load_plan).selectinload(LoadPlan.connection),
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        logger.error("execute_run: LoadRun %s not found", run_id,
                     extra={"run_id": run_id})
        return

    plan: LoadPlan = run.load_plan
    steps: list[LoadStep] = sorted(plan.load_steps, key=lambda s: s.sequence)

    # Bind workflow context for this background task (task-scoped, no reset needed).
    run_id_ctx_var.set(run_id)
    load_plan_id_ctx_var.set(str(plan.id))

    # Enrich the current tracing span with plan context once the run is loaded.
    from opentelemetry import trace as otel_trace
    from opentelemetry.trace import NonRecordingSpan
    _cur_span = otel_trace.get_current_span()
    if not isinstance(_cur_span, NonRecordingSpan):
        _cur_span.set_attribute("load_plan.id", str(plan.id))

    # ── Mark run as running ───────────────────────────────────────────────────
    run.status = RunStatus.running
    run.started_at = datetime.now(timezone.utc)
    await db.commit()

    record_run_started()
    _run_start = time.perf_counter()

    await publish_run_started(run_id)
    logger.info(
        "Run %s started: plan=%s steps=%d max_parallel_jobs=%d",
        run_id,
        plan.id,
        len(steps),
        plan.max_parallel_jobs,
        extra={"event_name": RunEvent.STARTED, "run_id": run_id,
               "load_plan_id": str(plan.id)},
    )

    # SFBL-112: try/finally backstop. If any code below exits the function
    # without transitioning the run out of ``running`` state (e.g. a coroutine
    # is cancelled, a helper raises before the normal finalisation path runs),
    # the finally block marks it failed via a fresh session so the run never
    # stays stuck.
    try:
        await _execute_run_body(
            run_id=run_id,
            run=run,
            plan=plan,
            steps=steps,
            db=db,
            db_factory=db_factory,
            _get_token=_get_token,
            _BulkClient=_BulkClient,
            _get_storage=_get_storage,
            _partition=_partition,
            _run_start=_run_start,
            _cur_span=_cur_span,
            NonRecordingSpan=NonRecordingSpan,
        )
    except asyncio.CancelledError:
        # Cancellation can fire anywhere in the body — including phases the
        # step-loop handler does NOT cover (preflight I/O, token fetch, step
        # event publishing). If the step-loop handler already marked the run
        # aborted, ``_mark_run_aborted_fresh`` is idempotent (overwrites
        # completed_at with a fresh value but status was already aborted).
        # If not, this is where the transition happens. Without this,
        # cancellations in those phases would reach the ``finally`` backstop
        # with status still ``running`` and be misclassified as ``failed`` /
        # ``unknown_exit`` rather than ``aborted``.
        logger.warning(
            "Run %s: cancelled — marking run aborted before re-raising",
            run_id,
            extra={"event_name": RunEvent.ABORTED,
                   "outcome_code": OutcomeCode.ABORTED,
                   "run_id": run_id},
        )
        await _mark_run_aborted_fresh(run_id, db_factory)
        try:
            await publish_run_aborted(run_id, reason="cancelled")
        except Exception:  # pragma: no cover - best-effort
            pass
        raise
    finally:
        # Backstop: re-fetch run in a fresh session and, if still ``running``,
        # mark it failed with an ``unknown_exit`` marker so operators can see
        # something went wrong even if no explicit handler fired.
        await _backstop_mark_failed_if_running(run_id, db_factory)


async def _execute_run_body(
    *,
    run_id: str,
    run: LoadRun,
    plan: LoadPlan,
    steps: list[LoadStep],
    db: AsyncSession,
    db_factory: _DbFactory,
    _get_token: Callable,
    _BulkClient: type,
    _get_storage: Callable,
    _partition: Callable,
    _run_start: float,
    _cur_span,
    NonRecordingSpan: type,
) -> None:
    """Main body of ``_execute_run`` (SFBL-112): separated so the caller can
    wrap it in a try/finally backstop."""
    # ── Pre-count total records across all steps ──────────────────────────────
    # Failures here do NOT abort the run — we proceed with an approximate count
    # and surface warnings on the run record so operators can see that the
    # displayed total_records is incomplete. See SFBL-110.
    preflight_warnings: list[dict] = []
    _preflight_start = time.perf_counter()
    logger.info(
        "Run %s: preflight started (steps=%d)",
        run_id, len(steps),
        extra={"event_name": RunEvent.PREFLIGHT_STARTED, "run_id": run_id,
               "load_plan_id": str(plan.id)},
    )
    try:
        preflight_total = 0
        for step in steps:
            try:
                storage = await _get_storage(step.input_connection_id, db)
                rel_paths = storage.discover_files(step.csv_file_pattern)
                for rel_path in rel_paths:
                    with storage.open_text(rel_path) as fh:
                        reader = csv.reader(fh)
                        try:
                            next(reader)  # skip header
                        except StopIteration:
                            continue
                        preflight_total += sum(1 for _ in reader)
            except InputStorageError as exc:
                logger.warning(
                    "Run %s: pre-count failed for step %s (storage error): %s",
                    run_id, step.id, exc,
                    extra={"event_name": RunEvent.PREFLIGHT_FAILED,
                           "outcome_code": OutcomeCode.STORAGE_ERROR,
                           "run_id": run_id, "step_id": str(step.id)},
                )
                record_run_preflight_failure(OutcomeCode.STORAGE_ERROR)
                preflight_warnings.append({
                    "step_id": str(step.id),
                    "outcome_code": OutcomeCode.STORAGE_ERROR,
                    "error": str(exc),
                })
            except Exception as exc:
                logger.warning(
                    "Run %s: pre-count failed for step %s (unexpected): %s",
                    run_id, step.id, exc,
                    extra={"event_name": RunEvent.PREFLIGHT_FAILED,
                           "outcome_code": OutcomeCode.UNEXPECTED_EXCEPTION,
                           "run_id": run_id, "step_id": str(step.id)},
                )
                record_run_preflight_failure(OutcomeCode.UNEXPECTED_EXCEPTION)
                preflight_warnings.append({
                    "step_id": str(step.id),
                    "outcome_code": OutcomeCode.UNEXPECTED_EXCEPTION,
                    "error": str(exc),
                })
        if preflight_total > 0:
            run.total_records = preflight_total
        if preflight_warnings:
            _merge_run_error_summary(run, {"preflight_warnings": preflight_warnings})
        if preflight_total > 0 or preflight_warnings:
            await db.commit()
    except Exception as exc:
        # Belt-and-braces catch in case anything outside the per-step loop fails
        # (e.g. db.commit itself). Still structured, still non-fatal.
        logger.warning(
            "Run %s: pre-count block failed: %s",
            run_id, exc,
            extra={"event_name": RunEvent.PREFLIGHT_FAILED,
                   "outcome_code": OutcomeCode.UNEXPECTED_EXCEPTION,
                   "run_id": run_id},
        )
        record_run_preflight_failure(OutcomeCode.UNEXPECTED_EXCEPTION)
    logger.info(
        "Run %s: preflight completed (total_records=%s, warnings=%d, duration_s=%.3f)",
        run_id, run.total_records, len(preflight_warnings),
        time.perf_counter() - _preflight_start,
        extra={"event_name": RunEvent.PREFLIGHT_COMPLETED,
               "outcome_code": (
                   OutcomeCode.DEGRADED if preflight_warnings else OutcomeCode.OK
               ),
               "run_id": run_id, "load_plan_id": str(plan.id)},
    )

    # ── Obtain Salesforce access token ────────────────────────────────────────
    try:
        access_token = await _get_token(db, plan.connection)
    except Exception as exc:
        logger.error("Run %s: failed to obtain access token: %s", run_id, exc,
                     extra={"event_name": RunEvent.FAILED, "outcome_code": OutcomeCode.AUTH_ERROR,
                            "run_id": run_id})
        await _mark_run_failed(run_id, db, error_summary={"auth_error": str(exc)})
        await publish_run_failed(run_id, error=str(exc))
        record_run_completed(RunStatus.failed.value, time.perf_counter() - _run_start)
        return

    semaphore = asyncio.Semaphore(plan.max_parallel_jobs)
    run_total_records = 0
    run_total_success = 0
    run_total_errors = 0
    any_step_failed = False

    # ── Execute steps in sequence ─────────────────────────────────────────────
    async with _BulkClient(plan.connection.instance_url, access_token) as bulk_client:
        for step in steps:
            # Reload run to detect external abort before starting next step.
            await db.refresh(run)
            if run.status == RunStatus.aborted:
                logger.info("Run %s: aborted before step %s", run_id, step.id,
                            extra={"event_name": RunEvent.ABORTED,
                                   "outcome_code": OutcomeCode.ABORTED,
                                   "run_id": run_id, "step_id": str(step.id)})
                await publish_run_aborted(run_id)
                record_run_completed(RunStatus.aborted.value, time.perf_counter() - _run_start)
                return

            await publish_step_started(
                run_id,
                step_id=step.id,
                object_name=step.object_name,
                sequence=step.sequence,
            )
            logger.info(
                "Run %s: starting step %d — %s %s",
                run_id,
                step.sequence,
                step.operation.value,
                step.object_name,
                extra={"event_name": StepEvent.STARTED, "run_id": run_id,
                       "step_id": str(step.id)},
            )

            try:
                step_success, step_errors = await _step_executor_mod.execute_step(
                    run_id=run_id,
                    step=step,
                    bulk_client=bulk_client,
                    db=db,
                    semaphore=semaphore,
                    db_factory=db_factory,
                    _get_storage=_get_storage,
                    _partition=_partition,
                )
            except InputStorageError as exc:
                logger.error(
                    "Run %s step %s: storage error: %s",
                    run_id,
                    step.id,
                    exc,
                    extra={"event_name": RunEvent.FAILED,
                           "outcome_code": OutcomeCode.STORAGE_ERROR,
                           "run_id": run_id, "step_id": str(step.id)},
                )
                await _mark_run_failed(
                    run_id, db, error_summary={"storage_error": str(exc)}
                )
                await publish_run_failed(run_id, error=str(exc))
                record_run_completed(RunStatus.failed.value, time.perf_counter() - _run_start)
                return
            except asyncio.CancelledError:
                # SFBL-112: external cancellation. Mark aborted via fresh
                # session (primary session may be mid-transaction), then
                # re-raise so the task shutdown proceeds normally.
                logger.warning(
                    "Run %s step %s: cancelled — marking run aborted",
                    run_id, step.id,
                    extra={"event_name": RunEvent.ABORTED,
                           "outcome_code": OutcomeCode.ABORTED,
                           "run_id": run_id, "step_id": str(step.id)},
                )
                await _mark_run_aborted_fresh(run_id, db_factory)
                await publish_run_aborted(run_id, reason="cancelled")
                record_run_completed(RunStatus.aborted.value, time.perf_counter() - _run_start)
                raise
            except Exception as exc:
                # SFBL-112: broad backstop for unhandled exceptions raised from
                # execute_step (programming errors, unexpected SDK failures).
                # Funnel through _mark_run_failed_fresh so the run never stays
                # stuck in ``running`` state.
                logger.exception(
                    "Run %s step %s: unhandled exception — marking run failed",
                    run_id, step.id,
                    extra={"event_name": RunEvent.FAILED,
                           "outcome_code": OutcomeCode.UNEXPECTED_EXCEPTION,
                           "run_id": run_id, "step_id": str(step.id)},
                )
                capture_exception(exc, outcome_code=OutcomeCode.UNEXPECTED_EXCEPTION)
                await _mark_run_failed_fresh(
                    run_id, db_factory,
                    error_summary={"unexpected_exception": str(exc)},
                )
                await publish_run_failed(run_id, error=str(exc))
                record_run_completed(RunStatus.failed.value, time.perf_counter() - _run_start)
                return

            total_step_records = step_success + step_errors
            run_total_records += total_step_records
            run_total_success += step_success
            run_total_errors += step_errors

            error_rate = (
                step_errors / total_step_records if total_step_records > 0 else 0.0
            )
            threshold = plan.error_threshold_pct / 100.0
            step_failed = error_rate > threshold

            await publish_step_completed(
                run_id,
                step_id=step.id,
                object_name=step.object_name,
                records_processed=total_step_records,
                records_success=step_success,
                records_failed=step_errors,
                step_failed=step_failed,
            )

            # Emit a structured progress event for observability (SFBL-59).
            steps_completed = steps.index(step) + 1
            logger.info(
                "Run %s: progress after step %d/%d — success=%d errors=%d",
                run_id,
                steps_completed,
                len(steps),
                run_total_success,
                run_total_errors,
                extra={
                    "event_name": RunEvent.PROGRESS_UPDATED,
                    "run_id": run_id,
                    "load_plan_id": str(plan.id),
                    "steps_total": len(steps),
                    "steps_completed": steps_completed,
                    "records_processed": run_total_records,
                    "records_succeeded": run_total_success,
                    "records_failed": run_total_errors,
                },
            )

            if step_failed:
                any_step_failed = True
                logger.warning(
                    "Run %s step %s exceeded error threshold: %.1f%% > %.1f%%",
                    run_id,
                    step.id,
                    error_rate * 100,
                    plan.error_threshold_pct,
                    extra={"event_name": StepEvent.THRESHOLD_EXCEEDED,
                           "outcome_code": OutcomeCode.STEP_THRESHOLD_EXCEEDED,
                           "run_id": run_id, "step_id": str(step.id)},
                )
                if plan.abort_on_step_failure:
                    logger.error(
                        "Run %s: aborting after step failure (abort_on_step_failure=True)",
                        run_id,
                        extra={"event_name": RunEvent.ABORTED,
                               "outcome_code": OutcomeCode.STEP_THRESHOLD_EXCEEDED,
                               "run_id": run_id, "step_id": str(step.id)},
                    )
                    await _abort_remaining_jobs(run_id, db, bulk_client)
                    run.status = RunStatus.aborted
                    run.completed_at = datetime.now(timezone.utc)
                    run.total_records = run_total_records
                    run.total_success = run_total_success
                    run.total_errors = run_total_errors
                    await db.commit()
                    record_run_completed(RunStatus.aborted.value, time.perf_counter() - _run_start)
                    await publish_run_aborted(run_id, reason="step_failure_threshold")
                    return

    # ── Finalise run ──────────────────────────────────────────────────────────
    final_status = (
        RunStatus.completed_with_errors if any_step_failed else RunStatus.completed
    )
    run.status = final_status
    run.completed_at = datetime.now(timezone.utc)
    run.total_records = run_total_records
    run.total_success = run_total_success
    run.total_errors = run_total_errors
    await db.commit()

    await publish_run_completed(
        run_id,
        status=final_status.value,
        total_records=run_total_records,
        total_success=run_total_success,
        total_errors=run_total_errors,
    )
    outcome = OutcomeCode.DEGRADED if any_step_failed else OutcomeCode.OK
    record_run_completed(final_status.value, time.perf_counter() - _run_start)
    if not isinstance(_cur_span, NonRecordingSpan):
        _cur_span.set_attribute("outcome.code", outcome)
    logger.info(
        "Run %s completed (%s): records=%d success=%d errors=%d",
        run_id,
        final_status.value,
        run_total_records,
        run_total_success,
        run_total_errors,
        extra={"event_name": RunEvent.COMPLETED, "outcome_code": outcome,
               "run_id": run_id, "load_plan_id": str(plan.id)},
    )


async def _abort_remaining_jobs(
    run_id: str,
    db: AsyncSession,
    bulk_client: SalesforceBulkClient,
) -> None:
    """Call ``abort_job()`` on any in-flight SF jobs; mark pending ones aborted."""
    result = await db.execute(
        select(JobRecord).where(
            JobRecord.load_run_id == run_id,
            JobRecord.status.in_(
                [
                    JobStatus.uploading,
                    JobStatus.upload_complete,
                    JobStatus.in_progress,
                ]
            ),
        )
    )
    active_jobs = list(result.scalars().all())

    for job in active_jobs:
        if job.sf_job_id:
            try:
                await bulk_client.abort_job(job.sf_job_id)
            except BulkAPIError as exc:
                logger.warning(
                    "Run %s: could not abort SF job %s: %s",
                    run_id,
                    job.sf_job_id,
                    exc,
                    extra={"run_id": run_id, "sf_job_id": job.sf_job_id},
                )
        job.status = JobStatus.aborted
        job.completed_at = datetime.now(timezone.utc)

    # Mark pending jobs aborted (not yet submitted to Salesforce).
    await db.execute(
        update(JobRecord)
        .where(
            JobRecord.load_run_id == run_id,
            JobRecord.status == JobStatus.pending,
        )
        .values(status=JobStatus.aborted)
    )
    await db.commit()


def _merge_run_error_summary(run: LoadRun, updates: dict) -> None:
    """Shallow-merge ``updates`` into ``run.error_summary`` (JSON string column).

    Preserves existing keys (e.g. preflight warnings already recorded) when a
    later failure path writes new keys (e.g. ``auth_error``). Callers must still
    commit the session.
    """
    existing: dict = {}
    if run.error_summary:
        try:
            parsed = json.loads(run.error_summary)
            if isinstance(parsed, dict):
                existing = parsed
        except (json.JSONDecodeError, ValueError):
            existing = {}
    existing.update(updates)
    run.error_summary = json.dumps(existing)


async def _mark_run_failed(
    run_id: str,
    db: AsyncSession,
    *,
    error_summary: Optional[dict] = None,
) -> None:
    """Mark a run as ``failed`` with an optional JSON error summary.

    If ``error_summary`` is supplied, its keys are shallow-merged into any
    existing ``error_summary`` JSON on the run (e.g. preflight warnings written
    earlier in the run lifecycle). See ``_merge_run_error_summary``.
    """
    result = await db.execute(select(LoadRun).where(LoadRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        return
    run.status = RunStatus.failed
    run.completed_at = datetime.now(timezone.utc)
    if error_summary:
        _merge_run_error_summary(run, error_summary)
    await db.commit()


async def _backstop_mark_failed_if_running(
    run_id: str,
    db_factory: _DbFactory,
) -> None:
    """SFBL-112: final backstop run in the ``finally`` block of ``_execute_run``.

    Opens a fresh session, re-fetches the run, and marks it failed with an
    ``unknown_exit`` marker if it is still in ``running`` state. This catches
    scenarios where the main body exited without any explicit exception
    handler transitioning the run to a terminal state (e.g. a bug in a
    post-step helper, an unexpected ``return`` path, or a raised exception
    that bypassed all handlers).
    """
    try:
        async with db_factory() as fresh_db:
            result = await fresh_db.execute(
                select(LoadRun).where(LoadRun.id == run_id)
            )
            run = result.scalar_one_or_none()
            if run is None or run.status != RunStatus.running:
                return
            logger.error(
                "Run %s: finally backstop — run still running after execute_run "
                "body exited. Marking failed with unknown_exit.",
                run_id,
                extra={"event_name": RunEvent.FAILED,
                       "outcome_code": OutcomeCode.UNEXPECTED_EXCEPTION,
                       "run_id": run_id},
            )
            run.status = RunStatus.failed
            run.completed_at = datetime.now(timezone.utc)
            _merge_run_error_summary(
                run,
                {"unknown_exit": "run body exited without finalising status"},
            )
            await fresh_db.commit()
    except Exception as exc:  # pragma: no cover - best-effort backstop
        logger.exception(
            "Failed backstop for run %s: %s",
            run_id, exc,
            extra={"run_id": run_id, "outcome_code": OutcomeCode.DATABASE_ERROR},
        )


async def _mark_run_failed_fresh(
    run_id: str,
    db_factory: _DbFactory,
    *,
    error_summary: Optional[dict] = None,
) -> None:
    """Mark a run as ``failed`` using a freshly-opened session from ``db_factory``.

    Used by exception-handling paths where the primary session may already be
    in a broken-transaction state (SFBL-112).
    """
    try:
        async with db_factory() as fresh_db:
            await _mark_run_failed(run_id, fresh_db, error_summary=error_summary)
    except Exception as exc:  # pragma: no cover - best-effort backstop
        logger.exception(
            "Failed to mark run %s failed via fresh session: %s",
            run_id, exc,
            extra={"run_id": run_id, "outcome_code": OutcomeCode.DATABASE_ERROR},
        )


async def _mark_run_aborted_fresh(
    run_id: str,
    db_factory: _DbFactory,
) -> None:
    """Mark a run as ``aborted`` using a freshly-opened session from ``db_factory``.

    Used when the run is cancelled externally (asyncio.CancelledError) and the
    primary session may be unsafe to use (SFBL-112).
    """
    try:
        async with db_factory() as fresh_db:
            result = await fresh_db.execute(
                select(LoadRun).where(LoadRun.id == run_id)
            )
            run = result.scalar_one_or_none()
            if run is None:
                return
            run.status = RunStatus.aborted
            run.completed_at = datetime.now(timezone.utc)
            await fresh_db.commit()
    except Exception as exc:  # pragma: no cover - best-effort backstop
        logger.exception(
            "Failed to mark run %s aborted via fresh session: %s",
            run_id, exc,
            extra={"run_id": run_id, "outcome_code": OutcomeCode.DATABASE_ERROR},
        )
