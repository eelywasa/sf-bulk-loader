"""Per-partition Salesforce Bulk API execution: submit, poll, download results.

Each call to :func:`process_partition` creates its own ``AsyncSession`` and
acquires the run semaphore before touching the Salesforce API.  This module
owns all per-job DB status transitions and the polling loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.job import JobRecord, JobStatus
from app.models.load_run import LoadRun, RunStatus
from app.models.load_step import LoadStep
from app.observability.context import job_record_id_ctx_var, sf_job_id_ctx_var
from app.observability.events import JobEvent, OutcomeCode, SalesforceEvent
from app.observability import tracing
from app.services.output_storage import OutputStorage
from app.services.result_persistence import download_and_persist_results
from app.services.run_event_publisher import publish_job_status_change
from app.observability.metrics import record_bulk_job_poll_timeout
from app.services.salesforce_bulk import (
    BulkAPIError,
    BulkJobPollTimeout,
    SalesforceBulkClient,
    _TERMINAL_STATES,
)

logger = logging.getLogger(__name__)

_DbFactory = Callable[[], AsyncSession]


async def process_partition(
    *,
    run_id: str,
    step: LoadStep,
    job_record_id: str,
    csv_data: bytes,
    bulk_client: SalesforceBulkClient,
    semaphore: asyncio.Semaphore,
    db_factory: _DbFactory,
    output_storage: OutputStorage,
) -> tuple[int, int]:
    """Submit one CSV partition as a Bulk API 2.0 job and download results.

    Each call creates its own ``AsyncSession`` to avoid shared session state
    across concurrent coroutines.  The semaphore limits the number of
    partitions that may communicate with Salesforce at the same time.

    Returns:
        ``(success_count, error_count)`` — ``(0, 0)`` on early-exit failure paths.
    """
    # Bind job-scoped context so every log call in this task carries the IDs.
    job_record_id_ctx_var.set(job_record_id)

    with tracing.partition_span(job_record_id) as _partition_span:
        return await _process_partition_body(
            run_id=run_id,
            step=step,
            job_record_id=job_record_id,
            csv_data=csv_data,
            bulk_client=bulk_client,
            semaphore=semaphore,
            db_factory=db_factory,
            output_storage=output_storage,
            _partition_span=_partition_span,
        )


async def _process_partition_body(
    *,
    run_id: str,
    step: LoadStep,
    job_record_id: str,
    csv_data: bytes,
    bulk_client: SalesforceBulkClient,
    semaphore: asyncio.Semaphore,
    db_factory: _DbFactory,
    output_storage: OutputStorage,
    _partition_span,
) -> tuple[int, int]:
    async with db_factory() as db:
        job_rec = await db.get(JobRecord, job_record_id)
        if job_rec is None:
            logger.error("process_partition: JobRecord %s not found", job_record_id,
                         extra={"run_id": run_id, "job_record_id": job_record_id})
            return 0, 0

        async with semaphore:
            # Check for external abort before submitting to Salesforce.
            run_check = await db.get(LoadRun, run_id)
            if run_check and run_check.status == RunStatus.aborted:
                job_rec.status = JobStatus.aborted
                await db.commit()
                return 0, 0

            # ── Create SF job ─────────────────────────────────────────────────
            job_rec.status = JobStatus.uploading
            job_rec.started_at = datetime.now(timezone.utc)
            await db.commit()

            try:
                sf_job_id = await bulk_client.create_job(
                    step.object_name,
                    step.operation.value,
                    external_id_field=step.external_id_field or None,
                    assignment_rule_id=step.assignment_rule_id or None,
                )
            except BulkAPIError as exc:
                logger.error(
                    "Run %s step %s partition %d: create_job failed: %s",
                    run_id,
                    step.id,
                    job_rec.partition_index,
                    exc,
                    extra={"event_name": SalesforceEvent.BULK_JOB_FAILED,
                           "outcome_code": OutcomeCode.SALESFORCE_API_ERROR,
                           "run_id": run_id, "step_id": str(step.id),
                           "job_record_id": job_record_id},
                )
                job_rec.status = JobStatus.failed
                job_rec.error_message = str(exc) + (f"\nResponse: {exc.body}" if exc.body else "")
                job_rec.completed_at = datetime.now(timezone.utc)
                await db.commit()
                await publish_job_status_change(
                    run_id,
                    step_id=step.id,
                    job_id=job_record_id,
                    status=JobStatus.failed.value,
                )
                return 0, 0

            job_rec.sf_job_id = sf_job_id
            sf_job_id_ctx_var.set(sf_job_id)
            from opentelemetry.trace import NonRecordingSpan
            if not isinstance(_partition_span, NonRecordingSpan):
                _partition_span.set_attribute("salesforce.job.id", sf_job_id)
            await db.commit()
            await publish_job_status_change(
                run_id,
                step_id=step.id,
                job_id=job_record_id,
                sf_job_id=sf_job_id,
                status=JobStatus.uploading.value,
            )

            # ── Upload CSV ────────────────────────────────────────────────────
            try:
                await bulk_client.upload_csv(sf_job_id, csv_data)
                await bulk_client.close_job(sf_job_id)
            except BulkAPIError as exc:
                logger.error(
                    "Run %s step %s partition %d: upload/close failed: %s",
                    run_id,
                    step.id,
                    job_rec.partition_index,
                    exc,
                    extra={"event_name": SalesforceEvent.BULK_JOB_FAILED,
                           "outcome_code": OutcomeCode.SALESFORCE_API_ERROR,
                           "run_id": run_id, "step_id": str(step.id),
                           "job_record_id": job_record_id, "sf_job_id": sf_job_id},
                )
                job_rec.status = JobStatus.failed
                job_rec.error_message = str(exc) + (f"\nResponse: {exc.body}" if exc.body else "")
                job_rec.completed_at = datetime.now(timezone.utc)
                await db.commit()
                await publish_job_status_change(
                    run_id,
                    step_id=step.id,
                    job_id=job_record_id,
                    sf_job_id=sf_job_id,
                    status=JobStatus.failed.value,
                )
                return 0, 0

            job_rec.status = JobStatus.upload_complete
            await db.commit()
            await publish_job_status_change(
                run_id,
                step_id=step.id,
                job_id=job_record_id,
                sf_job_id=sf_job_id,
                status=JobStatus.upload_complete.value,
            )

            # ── Poll job with mid-run progress updates ────────────────────────
            job_rec.status = JobStatus.in_progress
            await db.commit()

            try:
                interval = float(settings.sf_poll_interval_initial)
                max_interval = float(settings.sf_poll_interval_max)
                timeout_s = settings.sf_job_timeout_minutes * 60
                # SFBL-111: absolute cap on the poll loop. 0 = unbounded (opt-out).
                max_poll_s = int(settings.sf_job_max_poll_seconds)
                loop_start = asyncio.get_event_loop().time()
                timeout_warned = False
                last_processed = -1  # sentinel so first poll always writes
                last_state = ""

                while True:
                    state, processed, failed, sf_body = await bulk_client.poll_job_once(sf_job_id)
                    last_state = state

                    if processed != last_processed:
                        last_processed = processed
                        job_rec.records_processed = processed
                        job_rec.records_failed = failed
                        # Update run-level heartbeat for stuck-run detection (SFBL-59).
                        run_heartbeat = await db.get(LoadRun, run_id)
                        if run_heartbeat is not None:
                            run_heartbeat.last_heartbeat_at = datetime.now(timezone.utc)
                        await db.commit()
                        await publish_job_status_change(
                            run_id,
                            step_id=step.id,
                            job_id=job_record_id,
                            sf_job_id=sf_job_id,
                            status=JobStatus.in_progress.value,
                            records_processed=processed,
                            records_failed=failed,
                            total_records=job_rec.total_records,
                        )

                    if state in _TERMINAL_STATES:
                        terminal_state = state
                        job_rec.sf_api_response = json.dumps(sf_body)
                        break

                    elapsed = asyncio.get_event_loop().time() - loop_start
                    if not timeout_warned and elapsed >= timeout_s:
                        logger.warning(
                            "Run %s step %s partition %d: job %s still in progress "
                            "after %d min — continuing to poll (spec §9.1)",
                            run_id,
                            step.id,
                            job_rec.partition_index,
                            sf_job_id,
                            settings.sf_job_timeout_minutes,
                            extra={"event_name": SalesforceEvent.BULK_JOB_POLLED,
                                   "outcome_code": OutcomeCode.TIMEOUT,
                                   "run_id": run_id, "step_id": str(step.id),
                                   "job_record_id": job_record_id, "sf_job_id": sf_job_id},
                        )
                        timeout_warned = True

                    # SFBL-111: hard cap. Best-effort abort on Salesforce so we
                    # don't leave the job running after we've given up, then
                    # raise BulkJobPollTimeout — caught by the same
                    # ``except BulkAPIError`` branch below, which marks the
                    # JobRecord failed.
                    if max_poll_s > 0 and elapsed >= max_poll_s:
                        logger.error(
                            "Run %s step %s partition %d: job %s exceeded poll "
                            "timeout of %ds (last state=%s) — marking failed",
                            run_id,
                            step.id,
                            job_rec.partition_index,
                            sf_job_id,
                            max_poll_s,
                            last_state,
                            extra={"event_name": SalesforceEvent.BULK_JOB_POLL_TIMEOUT,
                                   "outcome_code": OutcomeCode.JOB_POLL_TIMEOUT,
                                   "run_id": run_id, "step_id": str(step.id),
                                   "job_record_id": job_record_id, "sf_job_id": sf_job_id},
                        )
                        record_bulk_job_poll_timeout()
                        try:
                            await bulk_client.abort_job(sf_job_id)
                        except BulkAPIError as abort_exc:
                            logger.warning(
                                "Run %s step %s partition %d: best-effort abort of "
                                "timed-out job %s failed: %s",
                                run_id, step.id, job_rec.partition_index,
                                sf_job_id, abort_exc,
                                extra={"run_id": run_id, "step_id": str(step.id),
                                       "job_record_id": job_record_id,
                                       "sf_job_id": sf_job_id},
                            )
                        raise BulkJobPollTimeout(
                            f"poll_job timed out for {sf_job_id} after {max_poll_s}s "
                            f"(last state={last_state})",
                            last_state=last_state,
                        )

                    await asyncio.sleep(interval)
                    interval = min(interval * 2.0, max_interval)

            except BulkAPIError as exc:
                logger.error(
                    "Run %s step %s partition %d: polling failed: %s",
                    run_id,
                    step.id,
                    job_rec.partition_index,
                    exc,
                    extra={"event_name": SalesforceEvent.BULK_JOB_FAILED,
                           "outcome_code": OutcomeCode.SALESFORCE_API_ERROR,
                           "run_id": run_id, "step_id": str(step.id),
                           "job_record_id": job_record_id, "sf_job_id": sf_job_id},
                )
                job_rec.status = JobStatus.failed
                job_rec.error_message = str(exc) + (f"\nResponse: {exc.body}" if exc.body else "")
                job_rec.completed_at = datetime.now(timezone.utc)
                await db.commit()
                await publish_job_status_change(
                    run_id,
                    step_id=step.id,
                    job_id=job_record_id,
                    sf_job_id=sf_job_id,
                    status=JobStatus.failed.value,
                )
                return 0, 0

            # ── Download results ──────────────────────────────────────────────
            records_processed, records_failed = await download_and_persist_results(
                bulk_client=bulk_client,
                sf_job_id=sf_job_id,
                job_record=job_rec,
                run_id=run_id,
                step_id=step.id,
                output_storage=output_storage,
            )

            status_map = {
                "JobComplete": JobStatus.job_complete,
                "Failed": JobStatus.failed,
                "Aborted": JobStatus.aborted,
            }
            final_status = status_map.get(terminal_state, JobStatus.failed)

            job_rec.status = final_status
            job_rec.records_processed = records_processed
            job_rec.records_failed = records_failed
            job_rec.completed_at = datetime.now(timezone.utc)
            await db.commit()

            await publish_job_status_change(
                run_id,
                step_id=step.id,
                job_id=job_record_id,
                sf_job_id=sf_job_id,
                status=final_status.value,
                records_processed=records_processed,
                records_failed=records_failed,
            )
            _job_event = (
                JobEvent.COMPLETED if terminal_state == "JobComplete" else JobEvent.FAILED
            )
            _job_outcome = OutcomeCode.OK if terminal_state == "JobComplete" else (
                OutcomeCode.ABORTED if terminal_state == "Aborted"
                else OutcomeCode.SALESFORCE_API_ERROR
            )
            logger.info(
                "Run %s step %s partition %d: %s (processed=%d, failed=%d)",
                run_id,
                step.id,
                job_rec.partition_index,
                terminal_state,
                records_processed,
                records_failed,
                extra={"event_name": _job_event, "outcome_code": _job_outcome,
                       "run_id": run_id, "step_id": str(step.id),
                       "job_record_id": job_record_id, "sf_job_id": sf_job_id},
            )
            return records_processed - records_failed, records_failed
