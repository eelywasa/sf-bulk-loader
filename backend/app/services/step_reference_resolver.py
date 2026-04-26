"""Resolver that maps a step's ``input_from_step_id`` to a concrete storage backend.

Given a :class:`~app.models.load_step.LoadStep` whose ``input_from_step_id`` is
set, :func:`resolve_step_input` locates the upstream step's ``JobRecord`` for the
current run, reads its ``success_file_path``, and constructs the appropriate
:class:`~app.services.input_storage.BaseInputStorage` instance so that the
downstream step executor can stream the file without knowing whether the upstream
output lived on disk or in S3.

This is the S2 service layer for SFBL-166 (named step outputs / step chaining).
Wiring into ``step_executor`` is covered by SFBL-263.
"""

from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.input_connection import InputConnection
from app.models.job import JobRecord
from app.services.input_storage import (
    BaseInputStorage,
    InputStorageError,
    LocalInputStorage,
    S3InputStorage,
)
from app.utils.encryption import decrypt_secret

if TYPE_CHECKING:
    from app.models.load_plan import LoadPlan
    from app.models.load_step import LoadStep


# ── Error class ───────────────────────────────────────────────────────────────


class StepReferenceResolutionError(InputStorageError):
    """Raised when an upstream step's artefact cannot be located.

    Subclasses :class:`~app.services.input_storage.InputStorageError` so that the
    existing ``InputStorageError`` catch in
    ``run_coordinator._execute_run_body`` handles it automatically — SFBL-263
    needs no additional exception-handling changes.
    """


# ── Resolver ──────────────────────────────────────────────────────────────────


async def resolve_step_input(
    step: "LoadStep",
    run_id: str,
    plan: "LoadPlan",
    db: AsyncSession,
) -> tuple[BaseInputStorage, list[str]]:
    """Resolve ``step.input_from_step_id`` to a ``(storage, rel_paths)`` pair.

    Locates the upstream step's :class:`~app.models.job.JobRecord` for
    *run_id* at ``partition_index=0`` (query steps produce a single output
    partition), reads its ``success_file_path``, and constructs the correct
    storage backend.

    Args:
        step: The downstream :class:`~app.models.load_step.LoadStep` whose
            ``input_from_step_id`` points to the upstream step.
        run_id: ID of the current :class:`~app.models.load_run.LoadRun`.
        plan: The :class:`~app.models.load_plan.LoadPlan` that owns both steps.
            Its ``output_connection_id`` determines whether the upstream wrote
            to local disk or S3.
        db: Active async database session.

    Returns:
        A ``(storage, [rel_path])`` tuple where *storage* is a ready-to-use
        :class:`~app.services.input_storage.BaseInputStorage` instance and
        *rel_path* is the source-relative path of the upstream output file.

    Raises:
        :exc:`StepReferenceResolutionError`: If the upstream ``JobRecord``
            cannot be found for this run, if ``success_file_path`` is ``NULL``,
            if the S3 URI is malformed, or if the URI's bucket disagrees with
            the configured ``InputConnection``.
    """
    # ── 1. Look up the upstream JobRecord ────────────────────────────────────
    result = await db.execute(
        select(JobRecord).where(
            JobRecord.load_run_id == run_id,
            JobRecord.load_step_id == step.input_from_step_id,
            JobRecord.partition_index == 0,
        )
    )
    upstream_record: JobRecord | None = result.scalar_one_or_none()

    if upstream_record is None:
        raise StepReferenceResolutionError(
            f"No JobRecord found for upstream step {step.input_from_step_id!r} "
            f"in run {run_id!r} at partition_index=0"
        )

    # ── 2. Validate success_file_path ────────────────────────────────────────
    if upstream_record.success_file_path is None:
        raise StepReferenceResolutionError(
            f"Upstream step {step.input_from_step_id!r} in run {run_id!r} "
            "has no success_file_path (job may not have completed successfully)"
        )

    # ── 3. Construct the appropriate storage backend ─────────────────────────
    if plan.output_connection_id is None:
        # Local backend — success_file_path is already relative to output_dir.
        storage: BaseInputStorage = LocalInputStorage(settings.output_dir)
        rel_path = upstream_record.success_file_path
    else:
        # S3 backend — success_file_path is an s3:// URI written by the
        # output-storage layer (SFBL-115).  Parse it to recover the bucket and
        # full object key; use root_prefix="" because the full key already
        # includes the original root_prefix.
        parsed = urllib.parse.urlparse(upstream_record.success_file_path)

        if parsed.scheme != "s3":
            raise StepReferenceResolutionError(
                f"Upstream step {step.input_from_step_id!r} success_file_path "
                f"is not an s3:// URI: {upstream_record.success_file_path!r}"
            )

        uri_bucket = parsed.netloc
        # parsed.path always starts with "/" for hierarchical URIs; strip it.
        full_key = parsed.path.lstrip("/")

        if not uri_bucket or not full_key:
            raise StepReferenceResolutionError(
                f"Malformed S3 URI (missing bucket or key): "
                f"{upstream_record.success_file_path!r}"
            )

        # Verify the URI bucket still matches the InputConnection's bucket.
        ic: InputConnection | None = await db.get(InputConnection, plan.output_connection_id)
        if ic is None:
            raise StepReferenceResolutionError(
                f"output_connection_id {plan.output_connection_id!r} not found"
            )

        if uri_bucket != ic.bucket:
            raise StepReferenceResolutionError(
                f"S3 URI bucket {uri_bucket!r} does not match InputConnection "
                f"bucket {ic.bucket!r} — the connection may have been repointed "
                "between runs"
            )

        storage = S3InputStorage(
            bucket=uri_bucket,
            root_prefix="",  # full_key is already the complete object key
            region=ic.region,
            access_key_id=decrypt_secret(ic.access_key_id),
            secret_access_key=decrypt_secret(ic.secret_access_key),
            session_token=(
                decrypt_secret(ic.session_token) if ic.session_token else None
            ),
        )
        rel_path = full_key

    return storage, [rel_path]
