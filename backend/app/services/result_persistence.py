"""Result file persistence: download Bulk API result CSVs and count rows.

Isolates Salesforce I/O (downloading result files) from DB state transitions.
The caller is responsible for committing the session after this module mutates
the ``JobRecord`` path fields.
"""

from __future__ import annotations

import csv
import io
import logging
import pathlib

from app.models.job import JobRecord
from app.services.output_storage import OutputStorage, OutputStorageError
from app.services.salesforce_bulk import BulkAPIError, SalesforceBulkClient

logger = logging.getLogger(__name__)


def count_csv_rows(csv_bytes: bytes) -> int:
    """Return the number of *data* rows in a UTF-8 CSV (header row excluded).

    Handles quoted fields that contain embedded newlines correctly via the
    standard :mod:`csv` module.  Returns 0 for empty or header-only content.
    """
    if not csv_bytes or not csv_bytes.strip():
        return 0
    text = csv_bytes.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    try:
        next(reader)  # skip header
    except StopIteration:
        return 0
    return sum(1 for _ in reader)


async def download_and_persist_results(
    *,
    bulk_client: SalesforceBulkClient,
    sf_job_id: str,
    job_record: JobRecord,
    run_id: str,
    step_id: str,
    output_storage: OutputStorage,
) -> tuple[int, int]:
    """Download success / error / unprocessed CSVs and persist them via *output_storage*.

    The storage reference returned by ``output_storage.write_bytes`` (a local
    relative path or an ``s3://`` URI) is stored in the corresponding
    ``job_record.*_file_path`` field.  The caller must commit the session.

    Returns:
        ``(records_processed, records_failed)`` where *records_processed*
        includes both successes and failures.
    """
    idx = job_record.partition_index
    records_processed = 0
    records_failed = 0

    # ── Success results ───────────────────────────────────────────────────────
    try:
        success_csv = await bulk_client.get_success_results(sf_job_id)
        if success_csv:
            rel = str(pathlib.Path(run_id) / step_id / f"partition_{idx}_success.csv")
            try:
                ref = output_storage.write_bytes(rel, success_csv)
                job_record.success_file_path = ref
            except OutputStorageError as exc:
                logger.warning(
                    "Run %s partition %d: could not write success results for job %s: %s",
                    run_id,
                    idx,
                    sf_job_id,
                    exc,
                )
            records_processed += count_csv_rows(success_csv)
    except BulkAPIError as exc:
        logger.warning(
            "Run %s partition %d: could not download success results for job %s: %s",
            run_id,
            idx,
            sf_job_id,
            exc,
        )

    # ── Error results ─────────────────────────────────────────────────────────
    try:
        error_csv = await bulk_client.get_failed_results(sf_job_id)
        if error_csv:
            rel = str(pathlib.Path(run_id) / step_id / f"partition_{idx}_errors.csv")
            try:
                ref = output_storage.write_bytes(rel, error_csv)
                job_record.error_file_path = ref
            except OutputStorageError as exc:
                logger.warning(
                    "Run %s partition %d: could not write error results for job %s: %s",
                    run_id,
                    idx,
                    sf_job_id,
                    exc,
                )
            error_count = count_csv_rows(error_csv)
            records_failed += error_count
            records_processed += error_count
    except BulkAPIError as exc:
        logger.warning(
            "Run %s partition %d: could not download error results for job %s: %s",
            run_id,
            idx,
            sf_job_id,
            exc,
        )

    # ── Unprocessed results ───────────────────────────────────────────────────
    try:
        unprocessed_csv = await bulk_client.get_unprocessed_results(sf_job_id)
        if unprocessed_csv:
            rel = str(
                pathlib.Path(run_id) / step_id / f"partition_{idx}_unprocessed.csv"
            )
            try:
                ref = output_storage.write_bytes(rel, unprocessed_csv)
                job_record.unprocessed_file_path = ref
            except OutputStorageError as exc:
                logger.warning(
                    "Run %s partition %d: could not write unprocessed results for job %s: %s",
                    run_id,
                    idx,
                    sf_job_id,
                    exc,
                )
    except BulkAPIError as exc:
        logger.warning(
            "Run %s partition %d: could not download unprocessed results for job %s: %s",
            run_id,
            idx,
            sf_job_id,
            exc,
        )

    return records_processed, records_failed
