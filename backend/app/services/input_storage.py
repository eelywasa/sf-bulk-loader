"""Input storage service — single source of truth for local file operations.

Centralises path-safety validation, directory listing, CSV preview, row counting,
encoding detection, and glob-pattern discovery.  All file-browsing consumers
(the files API, step preview) delegate here rather than implementing their own.

Designed to match the storage abstraction interface in ``input-storage-spec.md``
so that a remote provider (e.g. ``S3InputStorage``) can be added alongside
``LocalInputStorage`` without rewiring callers.
"""

from __future__ import annotations

import boto3
import botocore.exceptions
import csv
import io
import logging
import os
import pathlib
from dataclasses import dataclass
from typing import IO, Optional, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.input_connection import InputConnection
from app.utils.encryption import decrypt_secret

logger = logging.getLogger(__name__)

# Encodings attempted during detection, in priority order.
# ``utf-8-sig`` handles UTF-8 with and without BOM and is tried first.
# ``cp1252`` (Windows-1252) is tried before ``latin-1`` because it is the most
# common non-UTF-8 encoding in practice.  ``latin-1`` is last because it accepts
# every byte value and never raises, making it the universal fallback.
_ENCODING_CANDIDATES: tuple[str, ...] = ("utf-8-sig", "cp1252", "latin-1")


# ── Exceptions ────────────────────────────────────────────────────────────────


class InputStorageError(Exception):
    """Raised for invalid paths, traversal attempts, or inaccessible resources."""


class InputConnectionNotFoundError(InputStorageError):
    """Raised when a referenced input connection does not exist."""


class UnsupportedInputProviderError(InputStorageError):
    """Raised when an input connection refers to an unsupported provider."""


# ── Data transfer objects ─────────────────────────────────────────────────────


@dataclass
class InputEntry:
    """A single directory entry returned by :meth:`LocalInputStorage.list_entries`."""

    name: str
    kind: str  # "file" | "directory"
    path: str  # relative to storage root
    size_bytes: Optional[int]
    row_count: Optional[int]


@dataclass
class InputPreview:
    """Paginated CSV preview returned by :meth:`BaseInputStorage.preview_file`."""

    filename: str
    header: list[str]
    rows: list[dict]            # rows for the current page only
    total_rows: int | None      # exact total file rows when known without an extra scan
    filtered_rows: int | None   # total rows matching active filters; None when no filters
    offset: int                 # 0-based row offset (header not counted)
    limit: int                  # page size requested
    has_next: bool              # whether at least one more row exists after this page
    row_count: int              # deprecated — always len(rows); retained for migration compat


class BaseInputStorage(Protocol):
    """Provider-neutral storage contract used by file-browsing consumers."""

    provider: str

    def list_entries(self, path: str = "") -> list[InputEntry]: ...

    def preview_file(
        self,
        path: str,
        limit: int = 50,
        offset: int = 0,
        filters: list[dict[str, str]] | None = None,
    ) -> InputPreview: ...

    def discover_files(self, glob_pattern: str) -> list[str]: ...

    def open_text(self, path: str) -> IO[str]: ...


# ── Encoding detection ────────────────────────────────────────────────────────


def detect_encoding_from_bytes(raw: bytes) -> str:
    """Return the most likely text encoding for *raw* bytes."""
    sample = raw[:65536]
    for enc in _ENCODING_CANDIDATES:
        try:
            sample.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return "latin-1"  # pragma: no cover — latin-1 never raises


def detect_encoding(file_path: pathlib.Path, sample_size: int = 65536) -> str:
    """Return the most likely text encoding for *file_path*.

    Reads the first *sample_size* bytes (default 64 KiB) and tries each
    encoding in :data:`_ENCODING_CANDIDATES` until one decodes without error.
    ``latin-1`` always succeeds and acts as the universal fallback.

    Args:
        file_path: Path to the file to inspect.
        sample_size: Number of bytes to sample.

    Returns:
        Encoding name suitable for ``open()`` / ``bytes.decode()``.
    """
    enc = detect_encoding_from_bytes(file_path.read_bytes()[:sample_size])
    logger.debug("Detected encoding %s for %s", enc, file_path.name)
    return enc


# ── Shared path helpers ───────────────────────────────────────────────────────


def _normalise_relative_path(path: str) -> str:
    """Return a provider-neutral relative path or raise InputStorageError."""
    normalised = path.replace("\\", "/").strip("/")
    if normalised in ("", "."):
        return ""
    pure = pathlib.PurePosixPath(normalised)
    if pure.is_absolute() or ".." in pure.parts:
        raise InputStorageError(f"Invalid path: {path!r}")
    return str(pure)


def _validate_glob_pattern(glob_pattern: str) -> str:
    """Return a provider-neutral glob pattern or raise InputStorageError."""
    normalised = glob_pattern.replace("\\", "/").strip("/")
    pure = pathlib.PurePosixPath(normalised)
    if pure.is_absolute() or ".." in pure.parts:
        raise InputStorageError(
            f"Pattern {glob_pattern!r} contains path traversal sequence '..'"
        )
    return str(pure)


def _matches_glob(path: str, glob_pattern: str) -> bool:
    """Match *path* against *glob_pattern* using pathlib-style semantics."""
    pure = pathlib.PurePosixPath(path)
    if pure.match(glob_pattern):
        return True
    if glob_pattern.startswith("**/"):
        return pure.match(glob_pattern[3:])
    return False


def _normalise_root_prefix(root_prefix: Optional[str]) -> str:
    """Return an S3 root prefix with trailing slash or an empty string."""
    if not root_prefix:
        return ""
    prefix = root_prefix.replace("\\", "/").strip("/")
    return f"{prefix}/" if prefix else ""


def _relative_key(key: str, root_prefix: str) -> str:
    """Return *key* relative to *root_prefix*."""
    return key[len(root_prefix) :] if key.startswith(root_prefix) else key


def _join_s3_key(root_prefix: str, rel_path: str) -> str:
    """Join root prefix and source-relative path into a full S3 object key."""
    return f"{root_prefix}{rel_path}" if rel_path else root_prefix


def _sort_entries(entries: list[InputEntry]) -> list[InputEntry]:
    """Return entries with directories first, then files, each sorted by name."""
    dirs = sorted((e for e in entries if e.kind == "directory"), key=lambda e: e.name)
    files = sorted((e for e in entries if e.kind == "file"), key=lambda e: e.name)
    return dirs + files


def _validate_filters(
    header: list[str],
    filters: list[dict[str, str]],
) -> list[tuple[str, str]]:
    """Validate and normalise filter dicts into ``(column, value)`` tuples.

    Args:
        header: CSV column names from the file being previewed.
        filters: Raw filter list from the caller; each entry must be a dict with
            ``"column"`` and ``"value"`` string keys.

    Returns:
        List of ``(column, value)`` tuples ready for :func:`_row_matches`.

    Raises:
        :exc:`InputStorageError`: If any filter is malformed, references an unknown
            column, has a blank column name, or duplicates a column.
    """
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for f in filters:
        if not isinstance(f, dict) or "column" not in f or "value" not in f:
            raise InputStorageError(
                "Each filter must be an object with 'column' and 'value' keys"
            )
        col = f["column"]
        val = f["value"]
        if not col:
            raise InputStorageError("Filter column name must not be blank")
        if col not in header:
            raise InputStorageError(
                f"Filter column {col!r} is not present in the file header"
            )
        if col in seen:
            raise InputStorageError(
                f"Duplicate filter column {col!r}; each column may appear at most once"
            )
        seen.add(col)
        result.append((col, val))
    return result


def _row_matches(row: dict, filter_tuples: list[tuple[str, str]]) -> bool:
    """Return True if *row* satisfies every filter in *filter_tuples*.

    Each filter is a case-insensitive substring match: the filter value must be
    contained within the cell value.  A ``None`` or missing cell never matches a
    non-empty filter value.
    """
    return all(
        fval.lower() in (row.get(fcol) or "").lower()
        for fcol, fval in filter_tuples
    )


# ── S3 streaming helper ───────────────────────────────────────────────────────


class _S3StreamingBodyReader(io.RawIOBase):
    """Adapts a boto3 ``StreamingBody`` with a prepended sample to ``io.RawIOBase``.

    The first *sample* bytes were read upfront for encoding detection.  This
    class re-emits them first, then continues reading from *body* on demand, so
    the full S3 object is accessible as a single sequential byte stream without
    loading it entirely into memory before CSV processing can begin.

    Args:
        body: boto3 ``StreamingBody`` (already partially consumed by the sample
            read).
        sample: Bytes already read from *body* for encoding detection.
    """

    def __init__(self, body, sample: bytes) -> None:
        self._prefix = io.BytesIO(sample)
        self._body = body

    def readinto(self, b: bytearray) -> int:  # type: ignore[override]
        # Drain the in-memory prefix first.
        n = self._prefix.readinto(b)
        if n > 0:
            return n
        # Then stream the remainder from S3 in caller-sized chunks.
        data = self._body.read(len(b))
        if not data:
            return 0
        n = len(data)
        b[:n] = data
        return n

    def readable(self) -> bool:
        return True


# ── Local storage implementation ──────────────────────────────────────────────


class LocalInputStorage:
    """Filesystem-backed input storage for local CSV files.

    All methods resolve paths relative to *input_dir* and enforce two-layer
    traversal protection:

    1. Reject any path whose components contain ``".."`` before touching the
       filesystem.
    2. Confirm that the resolved absolute path stays inside *input_dir* via
       :meth:`pathlib.Path.relative_to`.

    Args:
        input_dir: Absolute path to the root input directory.
    """

    provider = "local"

    def __init__(self, input_dir: str) -> None:
        self._base = pathlib.Path(input_dir).resolve()

    # ── Path safety ──────────────────────────────────────────────────────────

    def _safe_path(self, rel_path: str) -> Optional[pathlib.Path]:
        """Return the resolved :class:`~pathlib.Path` for *rel_path* if it is
        safe, otherwise ``None``.

        "Safe" means:
        - No ``".."`` component in the normalised path.
        - Resolved absolute path is inside :attr:`_base`.
        """
        try:
            safe_rel_path = _normalise_relative_path(rel_path)
        except InputStorageError:
            return None
        candidate = (self._base / safe_rel_path).resolve()
        try:
            candidate.relative_to(self._base)
        except ValueError:
            return None
        return candidate

    # ── Public interface ──────────────────────────────────────────────────────

    def list_entries(self, path: str = "") -> list[InputEntry]:
        """List CSV files and subdirectories at *path* within the base directory.

        Files beginning with ``"."`` are excluded.  Only ``.csv`` files are
        returned; other file types are silently skipped.  Directories appear
        before files.

        Args:
            path: Relative subdirectory path (empty string for the root).

        Returns:
            Sorted list of :class:`InputEntry` objects (directories first).

        Raises:
            :exc:`InputStorageError`: If *path* contains traversal sequences
                or does not resolve to an existing directory.
        """
        if path:
            target = self._safe_path(path)
            if target is None or not target.is_dir():
                raise InputStorageError(f"Invalid path: {path!r}")
        else:
            if not self._base.is_dir():
                return []
            target = self._base

        entries: list[InputEntry] = []

        try:
            with os.scandir(target) as it:
                for entry in sorted(it, key=lambda e: e.name):
                    if entry.name.startswith("."):
                        continue
                    rel = os.path.join(path, entry.name) if path else entry.name
                    rel = rel.replace("\\", "/")
                    if entry.is_dir(follow_symlinks=False):
                        entries.append(
                            InputEntry(
                                name=entry.name,
                                kind="directory",
                                path=rel,
                                size_bytes=None,
                                row_count=None,
                            )
                        )
                    elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(".csv"):
                        try:
                            size: Optional[int] = entry.stat().st_size
                        except OSError:
                            size = 0
                        row_count: Optional[int] = None
                        try:
                            with open(entry.path, encoding="utf-8-sig", errors="replace") as fh:
                                row_count = max(0, sum(1 for _ in fh) - 1)
                        except OSError:
                            pass
                        entries.append(
                            InputEntry(
                                name=entry.name,
                                kind="file",
                                path=rel,
                                size_bytes=size,
                                row_count=row_count,
                            )
                        )
        except OSError:
            return []

        return _sort_entries(entries)

    def preview_file(
        self,
        path: str,
        limit: int = 50,
        offset: int = 0,
        filters: list[dict[str, str]] | None = None,
    ) -> InputPreview:
        """Return a paginated, optionally filtered page of rows from a CSV file.

        Uses :func:`detect_encoding` so files encoded as cp1252 or latin-1 are
        handled correctly.

        Args:
            path: Relative path to the CSV file.
            limit: Maximum number of data rows to return for this page.
            offset: 0-based row offset into the (filtered) result set; header
                row is not counted.
            filters: Optional list of ``{"column": str, "value": str}`` dicts.
                Each filter is a case-insensitive substring match.  All filters
                are ANDed.  Validated against the file header before scanning.

        Returns:
            :class:`InputPreview` with pagination metadata.

        Raises:
            :exc:`InputStorageError`: If *path* is invalid, traversal is
                attempted, or a filter references an unknown / duplicate column.
            :exc:`FileNotFoundError`: If *path* does not exist or is not a file.
            :exc:`OSError`: If the file cannot be read.
        """
        resolved = self._safe_path(path)
        if resolved is None:
            raise InputStorageError(f"Invalid path: {path!r}")
        if not resolved.is_file():
            raise FileNotFoundError(f"File not found: {path!r}")

        enc = detect_encoding(resolved)

        active_filters = [f for f in (filters or []) if f]

        if active_filters:
            # Filtered path: full scan required to count matches accurately.
            with open(resolved, newline="", encoding=enc) as fh:
                reader = csv.DictReader(fh)
                header = list(reader.fieldnames or [])
                filter_tuples = _validate_filters(header, active_filters)
                total_scanned = 0
                match_count = 0
                page_rows: list[dict] = []
                for row in reader:
                    total_scanned += 1
                    if _row_matches(row, filter_tuples):
                        if match_count >= offset and len(page_rows) < limit:
                            page_rows.append(dict(row))
                        match_count += 1
            has_next = match_count > offset + limit
            return InputPreview(
                filename=path,
                header=header,
                rows=page_rows,
                total_rows=total_scanned,
                filtered_rows=match_count,
                offset=offset,
                limit=limit,
                has_next=has_next,
                row_count=len(page_rows),
            )

        # Unfiltered path: read only what is needed.
        with open(resolved, newline="", encoding=enc) as fh:
            reader = csv.DictReader(fh)
            header = list(reader.fieldnames or [])
            # Advance past offset rows without storing them.
            for _ in zip(range(offset), reader):
                pass
            # Read limit + 1 rows so we can detect whether another page exists.
            buffer: list[dict] = []
            for row in reader:
                buffer.append(dict(row))
                if len(buffer) == limit + 1:
                    break
        has_next = len(buffer) > limit
        page_rows = buffer[:limit]
        return InputPreview(
            filename=path,
            header=header,
            rows=page_rows,
            total_rows=None,
            filtered_rows=None,
            offset=offset,
            limit=limit,
            has_next=has_next,
            row_count=len(page_rows),
        )

    def discover_files(self, glob_pattern: str) -> list[str]:
        """Return source-relative files inside the base directory that match *glob_pattern*.

        Two-layer traversal protection is applied:

        1. Patterns containing ``".."`` are rejected before any filesystem access.
        2. Every matched candidate is validated to ensure its resolved path stays
           inside the base directory.

        Args:
            glob_pattern: Glob pattern relative to the base directory
                (e.g. ``"accounts_*.csv"`` or ``"subdir/**/*.csv"``).

        Returns:
            Sorted list of source-relative paths for regular files only.

        Raises:
            :exc:`InputStorageError`: If *glob_pattern* contains ``".."``.
        """
        safe_pattern = _validate_glob_pattern(glob_pattern)

        matched: list[str] = []
        for candidate in sorted(self._base.glob(safe_pattern)):
            if not candidate.is_file():
                continue
            try:
                candidate.resolve().relative_to(self._base)
            except ValueError:
                logger.warning(
                    "Skipping %s: resolved path escapes the input directory",
                    candidate,
                )
                continue
            matched.append(candidate.relative_to(self._base).as_posix())

        logger.info(
            "discover_files: pattern=%r dir=%s matched %d file(s)",
            safe_pattern,
            self._base,
            len(matched),
        )
        return matched

    def open_text(self, path: str) -> IO[str]:
        """Open *path* for sequential text reading with encoding auto-detection.

        The caller is responsible for closing the returned file object (use as
        a context manager).

        Args:
            path: Relative path to the file within the base directory.

        Returns:
            Opened text file handle.

        Raises:
            :exc:`InputStorageError`: If *path* is invalid or attempts traversal.
            :exc:`FileNotFoundError`: If *path* does not exist or is not a file.
        """
        resolved = self._safe_path(path)
        if resolved is None:
            raise InputStorageError(f"Invalid path: {path!r}")
        if not resolved.is_file():
            raise FileNotFoundError(f"File not found: {path!r}")
        enc = detect_encoding(resolved)
        return open(resolved, encoding=enc, newline="")


class S3InputStorage:
    """S3-backed input storage rooted at a bucket and optional prefix."""

    provider = "s3"

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
        client_kwargs = {
            "service_name": "s3",
            "aws_access_key_id": access_key_id,
            "aws_secret_access_key": secret_access_key,
            "region_name": region,
        }
        if session_token:
            client_kwargs["aws_session_token"] = session_token
        self._client = boto3.client(**client_kwargs)

    def _safe_relative_path(self, path: str) -> str:
        return _normalise_relative_path(path)

    def _get_object_bytes(self, path: str) -> bytes:
        rel_path = self._safe_relative_path(path)
        key = _join_s3_key(self._root_prefix, rel_path)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except botocore.exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"NoSuchKey", "404"}:
                raise FileNotFoundError(f"File not found: {path!r}") from exc
            raise InputStorageError(f"Could not read S3 object {path!r}: {exc}") from exc
        return response["Body"].read()

    def list_entries(self, path: str = "") -> list[InputEntry]:
        rel_path = self._safe_relative_path(path)
        prefix = _join_s3_key(self._root_prefix, rel_path)
        if prefix and not prefix.endswith("/"):
            prefix = f"{prefix}/"

        entries: list[InputEntry] = []
        try:
            response = self._client.list_objects_v2(
                Bucket=self._bucket,
                Prefix=prefix,
                Delimiter="/",
            )
        except botocore.exceptions.ClientError as exc:
            raise InputStorageError(f"Could not list S3 path {path!r}: {exc}") from exc

        for common_prefix in response.get("CommonPrefixes", []):
            key = common_prefix.get("Prefix", "")
            rel_key = _relative_key(key.rstrip("/"), self._root_prefix)
            name = pathlib.PurePosixPath(rel_key).name
            if not name or name.startswith("."):
                continue
            entries.append(
                InputEntry(
                    name=name,
                    kind="directory",
                    path=rel_key,
                    size_bytes=None,
                    row_count=None,
                )
            )

        for item in response.get("Contents", []):
            key = item.get("Key", "")
            if not key or key.endswith("/"):
                continue
            rel_key = _relative_key(key, self._root_prefix)
            if "/" in rel_key[len(rel_path) + 1 :] if rel_path else "/" in rel_key:
                continue
            name = pathlib.PurePosixPath(rel_key).name
            if name.startswith(".") or not name.lower().endswith(".csv"):
                continue
            entries.append(
                InputEntry(
                    name=name,
                    kind="file",
                    path=rel_key,
                    size_bytes=item.get("Size"),
                    row_count=None,
                )
            )

        return _sort_entries(entries)

    def preview_file(
        self,
        path: str,
        limit: int = 50,
        offset: int = 0,
        filters: list[dict[str, str]] | None = None,
    ) -> InputPreview:
        """Return a paginated, optionally filtered page of rows from an S3 CSV object.

        Uses :meth:`open_text` for streaming so the full object is never loaded
        into memory at once.

        Args:
            path: Source-relative path to the S3 object.
            limit: Maximum number of data rows to return for this page.
            offset: 0-based row offset into the (filtered) result set.
            filters: Optional list of ``{"column": str, "value": str}`` dicts.

        Returns:
            :class:`InputPreview` with pagination metadata.

        Raises:
            :exc:`InputStorageError`: If *path* is invalid or a filter is invalid.
            :exc:`FileNotFoundError`: If the S3 object does not exist.
        """
        filename = self._safe_relative_path(path)
        active_filters = [f for f in (filters or []) if f]

        if active_filters:
            with self.open_text(path) as fh:
                reader = csv.DictReader(fh)
                header = list(reader.fieldnames or [])
                filter_tuples = _validate_filters(header, active_filters)
                total_scanned = 0
                match_count = 0
                page_rows: list[dict] = []
                for row in reader:
                    total_scanned += 1
                    if _row_matches(row, filter_tuples):
                        if match_count >= offset and len(page_rows) < limit:
                            page_rows.append(dict(row))
                        match_count += 1
            has_next = match_count > offset + limit
            return InputPreview(
                filename=filename,
                header=header,
                rows=page_rows,
                total_rows=total_scanned,
                filtered_rows=match_count,
                offset=offset,
                limit=limit,
                has_next=has_next,
                row_count=len(page_rows),
            )

        with self.open_text(path) as fh:
            reader = csv.DictReader(fh)
            header = list(reader.fieldnames or [])
            for _ in zip(range(offset), reader):
                pass
            buffer: list[dict] = []
            for row in reader:
                buffer.append(dict(row))
                if len(buffer) == limit + 1:
                    break
        has_next = len(buffer) > limit
        page_rows = buffer[:limit]
        return InputPreview(
            filename=filename,
            header=header,
            rows=page_rows,
            total_rows=None,
            filtered_rows=None,
            offset=offset,
            limit=limit,
            has_next=has_next,
            row_count=len(page_rows),
        )

    def discover_files(self, glob_pattern: str) -> list[str]:
        safe_pattern = _validate_glob_pattern(glob_pattern)
        paginator = self._client.get_paginator("list_objects_v2")
        matched: list[str] = []

        try:
            for page in paginator.paginate(Bucket=self._bucket, Prefix=self._root_prefix):
                for item in page.get("Contents", []):
                    key = item.get("Key", "")
                    if not key or key.endswith("/"):
                        continue
                    rel_key = _relative_key(key, self._root_prefix)
                    if not rel_key.lower().endswith(".csv"):
                        continue
                    if _matches_glob(rel_key, safe_pattern):
                        matched.append(rel_key)
        except botocore.exceptions.ClientError as exc:
            raise InputStorageError(f"Could not discover S3 files for {glob_pattern!r}: {exc}") from exc

        return sorted(matched)

    def open_text(self, path: str) -> IO[str]:
        """Open *path* for sequential text reading without loading the full object.

        Reads the first 64 KiB of the S3 object for encoding detection, then
        wraps the remaining stream so that CSV processing can read rows
        incrementally while keeping memory usage bounded.

        The returned handle must be used as a context manager (or closed
        explicitly) so that the underlying S3 connection is released.

        Raises:
            :exc:`FileNotFoundError`: If the object does not exist.
            :exc:`InputStorageError`: For any other S3 access failure.
        """
        rel_path = self._safe_relative_path(path)
        key = _join_s3_key(self._root_prefix, rel_path)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except botocore.exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"NoSuchKey", "404"}:
                raise FileNotFoundError(f"File not found: {path!r}") from exc
            raise InputStorageError(
                f"Could not read S3 object {path!r}: {exc}"
            ) from exc

        body = response["Body"]
        sample = body.read(65536)  # read just enough for encoding detection
        enc = detect_encoding_from_bytes(sample)
        raw = _S3StreamingBodyReader(body, sample)
        buffered = io.BufferedReader(raw, buffer_size=65536)
        return io.TextIOWrapper(buffered, encoding=enc, newline="")


async def get_storage(source: Optional[str], db: AsyncSession) -> BaseInputStorage:
    """Resolve *source* to the appropriate input storage provider."""
    if source in (None, "", "local"):
        return LocalInputStorage(settings.input_dir)

    ic = await db.get(InputConnection, source)
    if ic is None:
        raise InputConnectionNotFoundError(f"Input connection not found: {source}")
    if ic.provider != "s3":
        raise UnsupportedInputProviderError(
            f"Unsupported input connection provider: {ic.provider}"
        )

    return S3InputStorage(
        bucket=ic.bucket,
        root_prefix=ic.root_prefix,
        region=ic.region,
        access_key_id=decrypt_secret(ic.access_key_id),
        secret_access_key=decrypt_secret(ic.secret_access_key),
        session_token=decrypt_secret(ic.session_token) if ic.session_token else None,
    )
