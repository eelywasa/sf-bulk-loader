"""Output storage service — abstraction for writing result CSVs.

Mirrors the structure of ``input_storage.py``.  Two implementations are
provided:

- :class:`LocalOutputStorage` — writes to a local directory (current default).
- :class:`S3OutputStorage` — uploads to an S3 bucket via boto3 multipart.

The factory function :func:`get_output_storage` resolves the correct
implementation from an optional ``output_connection_id`` and an open
:class:`~sqlalchemy.ext.asyncio.AsyncSession`.
"""

from __future__ import annotations

import io
import logging
import pathlib
from typing import Optional, Protocol

import boto3
import botocore.exceptions
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.input_connection import InputConnection
from app.observability.events import OutcomeCode, StorageEvent
from app.services.input_storage import _join_s3_key, _normalise_root_prefix
from app.utils.encryption import decrypt_secret

logger = logging.getLogger(__name__)


# ── Exceptions ────────────────────────────────────────────────────────────────


class OutputStorageError(Exception):
    """Raised for general output-storage failures."""


class OutputConnectionNotFoundError(OutputStorageError):
    """Raised when a referenced output connection does not exist."""


class UnsupportedOutputProviderError(OutputStorageError):
    """Raised when an output connection refers to an unsupported provider."""


# ── Protocol ──────────────────────────────────────────────────────────────────


class OutputStorage(Protocol):
    """Provider-neutral write/read contract used by result-persistence consumers.

    ``relative_path`` is constructed by
    :func:`app.services.result_persistence._result_path` and has the form
    ``{plan_short_id}-{plan_slug}/{run_short_id}/{sequence:02d}_{object_slug}_{operation}/partition_{idx}_{type}.csv``.

    Returns:
        The persisted reference — a local relative path for
        :class:`LocalOutputStorage`, or an ``s3://bucket/key`` URI for
        :class:`S3OutputStorage`.
    """

    def write_bytes(self, relative_path: str, data: bytes) -> str: ...

    def read_bytes(self, ref: str) -> bytes: ...


# ── Local storage implementation ──────────────────────────────────────────────


class LocalOutputStorage:
    """Filesystem-backed output storage.

    Args:
        output_dir: Absolute (or relative) path to the root output directory.
            Intermediate directories are created automatically on first write.
    """

    def __init__(self, output_dir: str) -> None:
        self._output_dir = output_dir

    def write_bytes(self, relative_path: str, data: bytes) -> str:
        """Write *data* to ``output_dir / relative_path``.

        Intermediate directories are created if they do not exist
        (``mkdir(parents=True, exist_ok=True)``).

        Args:
            relative_path: Path relative to the output directory, e.g.
                ``1a2b3c4d-q1-migration/3f2a1b4c/01_account_upsert/partition_0_success.csv``.
            data: Raw bytes to write.

        Returns:
            The unchanged *relative_path* string (preserves current behaviour
            in ``result_persistence.py``).
        """
        dest = pathlib.Path(self._output_dir) / relative_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        logger.debug("LocalOutputStorage: wrote %d bytes to %s", len(data), dest)
        return relative_path

    def read_bytes(self, ref: str) -> bytes:
        dest = pathlib.Path(self._output_dir) / ref
        try:
            return dest.read_bytes()
        except FileNotFoundError as exc:
            raise OutputStorageError(f"Output file not found: {ref}") from exc
        except OSError as exc:
            raise OutputStorageError(f"Failed to read output file: {ref}: {exc}") from exc


# ── S3 storage implementation ─────────────────────────────────────────────────


class S3OutputStorage:
    """S3-backed output storage rooted at a bucket and optional prefix.

    Args:
        bucket: S3 bucket name.
        root_prefix: Optional key prefix (e.g. ``"results/"``).  Normalised to
            end with ``"/"`` or to ``""`` when empty/``None``.
        region: AWS region name (e.g. ``"us-east-1"``), or ``None`` to use the
            boto3 default.
        access_key_id: AWS access key ID (plain text, not encrypted).
        secret_access_key: AWS secret access key (plain text, not encrypted).
        session_token: Optional STS session token.
    """

    def __init__(
        self,
        *,
        bucket: str,
        root_prefix: Optional[str],
        region: Optional[str],
        access_key_id: str,
        secret_access_key: str,
        session_token: Optional[str] = None,
    ) -> None:
        self._bucket = bucket
        self._root_prefix = _normalise_root_prefix(root_prefix)
        client_kwargs: dict = {
            "service_name": "s3",
            "aws_access_key_id": access_key_id,
            "aws_secret_access_key": secret_access_key,
            "region_name": region,
        }
        if session_token:
            client_kwargs["aws_session_token"] = session_token
        self._client = boto3.client(**client_kwargs)

    def write_bytes(self, relative_path: str, data: bytes) -> str:
        """Upload *data* to S3 as ``root_prefix + relative_path``.

        Uses ``upload_fileobj`` which delegates to the boto3 S3 Transfer
        Manager.  Multipart upload kicks in automatically for objects larger
        than the boto3 default threshold (8 MiB).

        Args:
            relative_path: Key suffix relative to ``root_prefix``.
            data: Raw bytes to upload.

        Returns:
            The S3 URI of the uploaded object, e.g.
            ``s3://my-bucket/results/1a2b3c4d-q1-migration/3f2a1b4c/01_account_upsert/partition_0_success.csv``.

        Raises:
            :exc:`OutputStorageError`: If the upload fails with a
                ``botocore.exceptions.ClientError``.
        """
        key = _join_s3_key(self._root_prefix, relative_path)
        logger.debug(
            "Uploading output to S3: %s/%s", self._bucket, key,
            extra={
                "event_name": StorageEvent.OUTPUT_UPLOAD_STARTED,
                "outcome_code": None,
                "s3_bucket": self._bucket,
                "s3_key": key,
            },
        )
        try:
            self._client.upload_fileobj(io.BytesIO(data), self._bucket, key)
        except botocore.exceptions.ClientError as exc:
            logger.warning(
                "S3 output upload failed for %s/%s: %s", self._bucket, key, exc,
                extra={
                    "event_name": StorageEvent.OUTPUT_UPLOAD_FAILED,
                    "outcome_code": OutcomeCode.OUTPUT_UPLOAD_ERROR,
                },
            )
            raise OutputStorageError(
                f"Could not upload to S3 key {key!r}: {exc}"
            ) from exc
        logger.info(
            "Output uploaded to S3: s3://%s/%s (%d bytes)", self._bucket, key, len(data),
            extra={
                "event_name": StorageEvent.OUTPUT_UPLOAD_COMPLETED,
                "outcome_code": OutcomeCode.OK,
                "bytes_written": len(data),
            },
        )
        return f"s3://{self._bucket}/{key}"

    def read_bytes(self, ref: str) -> bytes:
        without_scheme = ref[len("s3://"):]
        bucket, key = without_scheme.split("/", 1)
        buf = io.BytesIO()
        try:
            self._client.download_fileobj(bucket, key, buf)
        except botocore.exceptions.ClientError as exc:
            raise OutputStorageError(f"Could not download s3://{bucket}/{key}: {exc}") from exc
        return buf.getvalue()


# ── Factory ───────────────────────────────────────────────────────────────────


async def get_output_storage(
    output_connection_id: Optional[str],
    db: AsyncSession,
) -> OutputStorage:
    """Resolve *output_connection_id* to the appropriate output storage provider.

    Args:
        output_connection_id: ID of an :class:`~app.models.input_connection.InputConnection`
            record with ``direction in ('out', 'both')``, or ``None`` / ``""``
            to use the local filesystem.
        db: Open async database session used to look up the connection record.

    Returns:
        A :class:`LocalOutputStorage` when *output_connection_id* is ``None``
        or empty, otherwise the appropriate provider implementation.

    Raises:
        :exc:`OutputConnectionNotFoundError`: If *output_connection_id* is set
            but no matching :class:`~app.models.input_connection.InputConnection`
            exists.
        :exc:`OutputStorageError`: If the found connection has
            ``direction == 'in'`` (defensive guard — the API layer is the
            primary validator).
        :exc:`UnsupportedOutputProviderError`: If the connection's ``provider``
            is not yet supported.
    """
    if not output_connection_id:
        return LocalOutputStorage(settings.output_dir)

    ic: Optional[InputConnection] = await db.get(InputConnection, output_connection_id)
    if ic is None:
        raise OutputConnectionNotFoundError(
            f"Output connection not found: {output_connection_id}"
        )

    if ic.direction not in ("out", "both"):
        raise OutputStorageError(
            f"Connection direction must be 'out' or 'both' (got {ic.direction!r})"
        )

    if ic.provider == "s3":
        return S3OutputStorage(
            bucket=ic.bucket,
            root_prefix=ic.root_prefix,
            region=ic.region,
            access_key_id=decrypt_secret(ic.access_key_id),
            secret_access_key=decrypt_secret(ic.secret_access_key),
            session_token=decrypt_secret(ic.session_token) if ic.session_token else None,
        )

    raise UnsupportedOutputProviderError(
        f"Unsupported output connection provider: {ic.provider!r}"
    )
