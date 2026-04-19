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
import re

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


# ── Path construction (SFBL-164) ──────────────────────────────────────────────

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def _slugify(text: str, max_len: int = 50) -> str:
    """Convert *text* to a lowercase, hyphen-delimited slug.

    Any run of characters outside ``[a-z0-9]`` collapses to a single ``-``.
    Leading/trailing hyphens are stripped.  The result is truncated to
    *max_len* characters and any trailing hyphen left by truncation is also
    stripped.  Empty results fall back to ``"unnamed"``.
    """
    slug = _SLUG_PATTERN.sub("-", (text or "").lower()).strip("-")
    return slug[:max_len].rstrip("-") or "unnamed"


def _result_path(
    *,
    plan_id: str,
    plan_name: str,
    run_id: str,
    sequence: int,
    object_name: str,
    operation: str,
    partition_index: int,
    suffix: str,
) -> str:
    """Build the human-readable relative path for a result CSV.

    Format::

        {plan_short_id}-{plan_slug}/{run_short_id}/
            {sequence:02d}_{object_slug}_{operation}/
            partition_{n}_{suffix}.csv

    ``{plan_short_id}`` is the first 8 characters of the plan UUID and
    guarantees per-plan uniqueness even when two plans share the same name.
    ``{run_short_id}`` is the first 8 characters of the run UUID and
    differentiates runs of the same plan.
    """
    plan_dir = f"{plan_id[:8]}-{_slugify(plan_name)}"
    run_short = run_id[:8]
    step_dir = f"{sequence:02d}_{_slugify(object_name)}_{operation}"
    return str(
        pathlib.Path(plan_dir)
        / run_short
        / step_dir
        / f"partition_{partition_index}_{suffix}.csv"
    )


async def download_and_persist_results(
    *,
    bulk_client: SalesforceBulkClient,
    sf_job_id: str,
    job_record: JobRecord,
    run_id: str,
    plan_id: str,
    plan_name: str,
    step_sequence: int,
    object_name: str,
    operation: str,
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

    def _path(suffix: str) -> str:
        return _result_path(
            plan_id=plan_id,
            plan_name=plan_name,
            run_id=run_id,
            sequence=step_sequence,
            object_name=object_name,
            operation=operation,
            partition_index=idx,
            suffix=suffix,
        )

    # ── Success results ───────────────────────────────────────────────────────
    try:
        success_csv = await bulk_client.get_success_results(sf_job_id)
        if success_csv:
            rel = _path("success")
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
            rel = _path("errors")
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
            rel = _path("unprocessed")
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
