"""Input storage service — single source of truth for local file operations.

Centralises path-safety validation, directory listing, CSV preview, row counting,
text decoding, and glob-pattern discovery.  All file-browsing consumers
(the files API, step preview) delegate here rather than implementing their own.

Designed to match the storage abstraction interface in ``input-storage-spec.md``
so that a remote provider (e.g. ``S3InputStorage``) can be added alongside
``LocalInputStorage`` without rewiring callers.
"""

from __future__ import annotations

import boto3
import botocore.exceptions
import codecs
import csv
import fnmatch
import io
import logging
import os
import pathlib
from dataclasses import dataclass
from typing import IO, Callable, Iterator, Optional, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.input_connection import InputConnection
from app.observability.events import OutcomeCode, StorageEvent
from app.utils.encryption import decrypt_secret

logger = logging.getLogger(__name__)

# SFBL-401: input is decoded as UTF-8 unless a step supplies an override.
#
# There is deliberately **no encoding detection**.  Inferring an encoding from a
# 64 KiB prefix and applying it to a whole stream is unsound by construction:
# the guess can be invalidated by any byte past the sample, and — far worse —
# a *wrong but valid* guess decodes cleanly and writes mojibake into Salesforce
# with no error at all.  That silent case is not hypothetical; it is what the
# official Salesforce Data Loader did to 25 Account records with the file that
# motivated this change.  See DECISIONS.md 032.
DEFAULT_ENCODING: str = "utf-8-sig"

#: Byte cap for the post-failure diagnostic (D1.10a).  Above this the message
#: degrades to naming the offending byte and offset only, rather than reading a
#: large object again to identify a candidate codec.
DIAGNOSTIC_MAX_BYTES: int = 8 * 1024 * 1024

#: Codecs offered to operators, and therefore the codecs the failure diagnostic
#: considers.  Mirrors ``app.models.load_step.InputEncoding``; duplicated as a
#: plain tuple to keep this module free of a model import.
_DIAGNOSTIC_CANDIDATES: tuple[str, ...] = ("utf-8-sig", "cp1252", "latin-1")

#: Chunk size for streaming decode.
_CHUNK_BYTES: int = 64 * 1024


def resolve_encoding(encoding: Optional[str]) -> str:
    """Return the codec to decode with: the override, or the UTF-8 default."""
    return encoding or DEFAULT_ENCODING


# ── Exceptions ────────────────────────────────────────────────────────────────


class InputStorageError(Exception):
    """Raised for invalid paths, traversal attempts, or inaccessible resources."""


class InputConnectionNotFoundError(InputStorageError):
    """Raised when a referenced input connection does not exist."""


class UnsupportedInputProviderError(InputStorageError):
    """Raised when an input connection refers to an unsupported provider."""


class InputDecodeError(InputStorageError):
    """Raised when an input file cannot be decoded with the resolved encoding.

    Subclasses :class:`InputStorageError` deliberately, so it flows through the
    existing ``except InputStorageError`` handler in the run coordinator and
    lands in the ``storage_error`` key of ``LoadRun.error_summary`` — a field
    that is already declared on ``RunErrorSummary``, so the failure is visible
    without depending on SFBL-402.  (Same rationale as
    ``StepReferenceResolutionError``.)

    Handlers that care about the distinction must test for this subclass
    *before* the generic ``InputStorageError`` branch and log
    ``outcome_code=input_decode_error``: ``storage_error`` means the source was
    unreachable, whereas this means the source was read perfectly and its bytes
    are not what we expected.  Different owners, different remedies.

    Attributes are structured so log sites never have to re-parse the message.
    """

    def __init__(
        self,
        message: str,
        *,
        path: str,
        encoding: str,
        byte_value: Optional[int] = None,
        byte_offset: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.path = path
        self.encoding = encoding
        self.byte_value = byte_value
        self.byte_offset = byte_offset


# ── Decoding ──────────────────────────────────────────────────────────────────


def _diagnose(
    reread: Optional[Callable[[], IO[bytes]]],
    failed_encoding: str,
) -> str:
    """Return a human-readable hint about what an undecodable file looks like.

    Runs **only** after a decode failure has already terminated the read, so a
    second pass costs nothing that matters.  It **diagnoses but never acts** —
    the caller still refuses the file.  Silently choosing the diagnosed codec
    is exactly the behaviour this module removed.

    Bounded three ways (D1.10a): capped at :data:`DIAGNOSTIC_MAX_BYTES`, read in
    chunks rather than materialised whole, and each candidate abandoned at its
    first failing byte rather than read to EOF.
    """
    if reread is None:
        return ""

    # ``latin-1`` is deliberately excluded: it never raises on any byte
    # sequence, so "it decodes cleanly as latin-1" is true of *every* file and
    # is evidence of nothing.  Recommending it would be actively harmful — a
    # latin-1 read is exactly what silently mojibaked 25 Account records in the
    # incident that motivated this work.  If no strict codec matches, the file
    # is malformed and the operator needs to hear that instead.
    candidates = [
        c
        for c in _DIAGNOSTIC_CANDIDATES
        if c != failed_encoding and c != "latin-1"
    ]
    if not candidates:
        return ""

    # One incremental decoder per candidate, all fed the same chunks.  A
    # candidate is dropped the moment it fails, so a wrong codec costs only the
    # bytes read up to its first bad byte — not a full pass per codec.
    try:
        decoders = {
            c: codecs.getincrementaldecoder(c)("strict") for c in candidates
        }
    except LookupError:  # pragma: no cover - candidates are static
        return ""

    read_bytes = 0
    try:
        with reread() as raw:
            while decoders:
                chunk = raw.read(_CHUNK_BYTES)
                if not chunk:
                    break
                read_bytes += len(chunk)
                if read_bytes > DIAGNOSTIC_MAX_BYTES:
                    mb = DIAGNOSTIC_MAX_BYTES // (1024 * 1024)
                    return (
                        f" File is larger than {mb} MB, so no encoding diagnosis "
                        f"was attempted."
                    )
                for name in list(decoders):
                    try:
                        decoders[name].decode(chunk)
                    except UnicodeDecodeError:
                        del decoders[name]
            for name in list(decoders):
                try:
                    decoders[name].decode(b"", final=True)
                except UnicodeDecodeError:
                    del decoders[name]
    except Exception:  # pragma: no cover - diagnosis must never mask the real error
        return ""

    for candidate in candidates:
        if candidate in decoders:
            return (
                f" The file decodes cleanly as {candidate} — if that is correct, "
                f"set Encoding on the step."
            )

    return (
        " No supported encoding decodes the whole file. It appears to contain "
        "mixed encodings and should be repaired at source."
    )


class _DecodingTextStream:
    """Streaming text reader that owns its decode loop.

    Exists because ``open_text`` *returns a handle* — decoding happens lazily
    inside the caller's read loop, so there is no ``try`` at the storage
    boundary that a :exc:`UnicodeDecodeError` would ever pass through.  Owning
    the loop is the only place the error can be caught, and it makes the
    reported byte offset exact **by construction**: ``TextIOWrapper.tell()``
    raises on a non-seekable S3 body, so an offset cannot be recovered after
    the fact.

    Reproduces ``newline=""`` semantics exactly — no translation, and ``\\r``,
    ``\\n`` and ``\\r\\n`` all terminate a line — because every caller hands this
    to :mod:`csv`, which corrupts quoted fields containing embedded newlines
    otherwise.

    Offsets count bytes *fed to the decoder* and are **not** adjusted for a
    ``utf-8-sig`` BOM: the cumulative count is already file-absolute.
    """

    def __init__(
        self,
        raw: IO[bytes],
        *,
        path: str,
        encoding: str,
        reread: Optional[Callable[[], IO[bytes]]] = None,
        chunk_size: int = _CHUNK_BYTES,
        errors: str = "strict",
    ) -> None:
        self._raw = raw
        self._path = path
        self._encoding = encoding
        self._reread = reread
        self._chunk_size = chunk_size
        # ``errors="replace"`` is used only by preview surfaces (D1.11), where
        # browsing must never raise; load paths always decode strictly.
        self._decoder = codecs.getincrementaldecoder(encoding)(errors=errors)
        self._buf = ""
        self._consumed = 0        # bytes handed to the decoder so far
        self._eof = False
        self._closed = False

    # -- internals ---------------------------------------------------------

    def _fill(self) -> bool:
        """Decode one more chunk into the buffer. Returns False at EOF."""
        if self._eof:
            return False
        chunk = self._raw.read(self._chunk_size)
        if not chunk:
            self._eof = True
            # flush any bytes the decoder is still holding
            try:
                self._buf += self._decoder.decode(b"", final=True)
            except UnicodeDecodeError as exc:
                raise self._decode_error(exc, b"") from exc
            return False
        try:
            self._buf += self._decoder.decode(chunk)
        except UnicodeDecodeError as exc:
            raise self._decode_error(exc, chunk) from exc
        self._consumed += len(chunk)
        return True

    def _decode_error(self, exc: UnicodeDecodeError, chunk: bytes) -> InputDecodeError:
        # ``exc.object`` is what the decoder was working on: any bytes it had
        # buffered from the previous call, followed by this chunk.  So the file
        # offset of exc.object[0] is (bytes consumed before this chunk) minus
        # that carried-over prefix.
        pending = max(len(exc.object) - len(chunk), 0)
        offset = self._consumed - pending + exc.start
        byte_value = exc.object[exc.start] if exc.start < len(exc.object) else None
        name = pathlib.PurePosixPath(self._path).name or self._path

        detail = f" (0x{byte_value:02x})" if byte_value is not None else ""
        message = (
            f"{name} is not valid {self._encoding}: byte{detail} at offset {offset} "
            f"could not be decoded."
        ) + _diagnose(self._reread, self._encoding)

        return InputDecodeError(
            message,
            path=self._path,
            encoding=self._encoding,
            byte_value=byte_value,
            byte_offset=offset,
        )

    # -- text IO surface ---------------------------------------------------

    def readline(self, limit: int = -1) -> str:  # noqa: ARG002 - csv never passes one
        while True:
            idx = self._find_terminator()
            if idx is not None:
                line, self._buf = self._buf[:idx], self._buf[idx:]
                return line
            if not self._fill():
                line, self._buf = self._buf, ""
                return line

    def _find_terminator(self) -> Optional[int]:
        """Index just past the first line terminator, or None if incomplete."""
        for i, ch in enumerate(self._buf):
            if ch == "\n":
                return i + 1
            if ch == "\r":
                if i + 1 < len(self._buf):
                    return i + 2 if self._buf[i + 1] == "\n" else i + 1
                # trailing '\r': can't tell '\r' from '\r\n' until more arrives
                if self._eof:
                    return i + 1
                return None
        return None

    def read(self, size: int = -1) -> str:
        if size is None or size < 0:
            while self._fill():
                pass
            out, self._buf = self._buf, ""
            return out
        while len(self._buf) < size and self._fill():
            pass
        out, self._buf = self._buf[:size], self._buf[size:]
        return out

    def __iter__(self) -> Iterator[str]:
        return self

    def __next__(self) -> str:
        line = self.readline()
        if line == "":
            raise StopIteration
        return line

    def __enter__(self) -> "_DecodingTextStream":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._raw.close()
        except Exception:  # pragma: no cover - best effort
            pass


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
        *,
        encoding: str | None = None,
    ) -> InputPreview: ...

    def discover_files(self, glob_pattern: str) -> list[str]: ...

    def open_text(self, path: str, *, encoding: str | None = None) -> IO[str]: ...


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


def _glob_match_parts(path_parts: list[str], pat_parts: list[str]) -> bool:
    """Anchored, depth-aware glob match of *path_parts* against *pat_parts*.

    Mirrors ``pathlib.Path.glob`` semantics (each non-``**`` pattern segment
    matches exactly one path segment; ``**`` matches zero or more segments) —
    unlike ``PurePosixPath.match``, which matches from the right and would let a
    root-only pattern like ``*.csv`` match a nested key ``sub/a.csv``.
    """
    if not pat_parts:
        return not path_parts
    head, *rest = pat_parts
    if head == "**":
        # ``**`` consumes zero or more path segments.
        for i in range(len(path_parts) + 1):
            if _glob_match_parts(path_parts[i:], rest):
                return True
        return False
    if not path_parts:
        return False
    if fnmatch.fnmatchcase(path_parts[0], head):
        return _glob_match_parts(path_parts[1:], rest)
    return False


def _matches_glob(path: str, glob_pattern: str) -> bool:
    """Match *path* against *glob_pattern* with anchored, depth-aware semantics.

    Used by :class:`S3InputStorage.discover_files` so S3 discovery honours the
    same glob depth as the local filesystem (``Path.glob``): ``*.csv`` matches
    only root-level keys, ``sub/*.csv`` exactly one level, ``**/*.csv``
    recursively. See SFBL-385 (Codex review) — promoting S3 to the default
    input source made this parity load-bearing.
    """
    path_parts = [p for p in path.split("/") if p]
    pat_parts = [p for p in glob_pattern.split("/") if p]
    return _glob_match_parts(path_parts, pat_parts)


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


# ── Keyless S3 client primitive (SFBL-385) ─────────────────────────────────────


def build_s3_client(
    *,
    region: Optional[str],
    access_key_id: Optional[str] = None,
    secret_access_key: Optional[str] = None,
    session_token: Optional[str] = None,
):
    """Construct a boto3 S3 client, with or without explicit credentials.

    This is the single keyless-S3 primitive shared by the implicit first-party
    storage resolution (SFBL-385) and ``InputConnection.use_task_role``
    (SFBL-295). When *access_key_id* / *secret_access_key* are omitted, **no**
    credential kwargs are passed and boto3 resolves credentials via its default
    chain — on ``aws_hosted`` that is the ECS task role. When both keys are
    supplied (the BYO-keys path), they are passed through unchanged.

    The keyless decision is made on identity (``is None``), **not** truthiness:
    the keyless path is taken only when *both* credentials are ``None``. A BYO
    connection with a blank/empty-string key is therefore passed through to
    boto3 (which rejects it) rather than silently falling back to the task role
    — that fail-open would let a misconfigured BYO connection read/write the
    first-party buckets on ``aws_hosted``. Supplying exactly one of the two is a
    programming error and raises.

    Args:
        region: AWS region name, or ``None`` for the boto3 default.
        access_key_id: AWS access key ID, or ``None`` for the keyless path.
        secret_access_key: AWS secret access key, or ``None`` for keyless.
        session_token: Optional STS session token (only used when keys are set).

    Returns:
        A configured boto3 S3 client.

    Raises:
        ValueError: If exactly one of *access_key_id* / *secret_access_key* is
            provided (an incomplete credential pair).
    """
    client_kwargs: dict = {"service_name": "s3", "region_name": region}
    has_access_key = access_key_id is not None
    has_secret_key = secret_access_key is not None
    if has_access_key != has_secret_key:
        raise ValueError(
            "Incomplete S3 credentials: supply both access_key_id and "
            "secret_access_key, or neither (keyless task-role access)."
        )
    if has_access_key and has_secret_key:
        client_kwargs["aws_access_key_id"] = access_key_id
        client_kwargs["aws_secret_access_key"] = secret_access_key
        if session_token:
            client_kwargs["aws_session_token"] = session_token
    return boto3.client(**client_kwargs)


def _s3_outcome_code(exc: "botocore.exceptions.ClientError") -> str:
    """Map an S3 ``ClientError`` to a canonical observability outcome code.

    ``AccessDenied`` / ``NoSuchBucket`` / ``NoSuchKey`` are read-access failures
    (``storage_error``); ``SlowDown`` / ``Throttling`` / HTTP 503 are throttling
    (``rate_limited``). See ``docs/observability.md`` (storage flow).
    """
    code = exc.response.get("Error", {}).get("Code", "")
    if code in {"SlowDown", "Throttling", "ThrottlingException", "503", "RequestTimeout"}:
        return OutcomeCode.RATE_LIMITED
    return OutcomeCode.STORAGE_ERROR


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
        *,
        encoding: str | None = None,
    ) -> InputPreview:
        """Return a paginated, optionally filtered page of rows from a CSV file.

        Decodes with *encoding* (default UTF-8) using ``errors="replace"``, so
        browsing **never raises** — see D1.11.  Preview is advisory: nothing
        read here reaches Salesforce, so leniency cannot corrupt data, and the
        two Files-page endpoints have no step on which an operator could set an
        encoding.  *Loads* stay strict.

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

        enc = resolve_encoding(encoding)

        active_filters = [f for f in (filters or []) if f]

        if active_filters:
            # Filtered path: full scan required to count matches accurately.
            with open(resolved, newline="", encoding=enc, errors="replace") as fh:
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
            )

        # Unfiltered path: read only what is needed.
        with open(resolved, newline="", encoding=enc, errors="replace") as fh:
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

    def open_text(self, path: str, *, encoding: str | None = None) -> IO[str]:
        """Open *path* for sequential text reading.

        Decodes with *encoding*, defaulting to UTF-8.  There is no encoding
        detection: a wrong-but-valid guess decodes cleanly and writes mojibake
        into Salesforce with no error at all, which is the failure this module
        exists to prevent (DECISIONS.md 032).

        The caller is responsible for closing the returned handle (use as a
        context manager).

        Args:
            path: Relative path to the file within the base directory.
            encoding: Codec override; ``None`` means the UTF-8 default.

        Returns:
            A streaming text handle that raises :exc:`InputDecodeError` — never
            a bare :exc:`UnicodeDecodeError` — on undecodable input.

        Raises:
            :exc:`InputStorageError`: If *path* is invalid or attempts traversal.
            :exc:`FileNotFoundError`: If *path* does not exist or is not a file.
        """
        resolved = self._safe_path(path)
        if resolved is None:
            raise InputStorageError(f"Invalid path: {path!r}")
        if not resolved.is_file():
            raise FileNotFoundError(f"File not found: {path!r}")
        enc = resolve_encoding(encoding)
        return _DecodingTextStream(
            open(resolved, "rb"),
            path=path,
            encoding=enc,
            reread=lambda: open(resolved, "rb"),
        )


class S3InputStorage:
    """S3-backed input storage rooted at a bucket and optional prefix."""

    provider = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        root_prefix: Optional[str],
        region: Optional[str],
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        session_token: Optional[str] = None,
    ) -> None:
        self._bucket = bucket
        self._root_prefix = _normalise_root_prefix(root_prefix)
        # Keyless when no credentials are supplied — boto3 resolves via the ECS
        # task-role chain (SFBL-385). BYO keys are passed through unchanged.
        self._client = build_s3_client(
            region=region,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
        )

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
        # Paginate via the continuation token. ListObjectsV2 returns at most
        # 1,000 keys/CommonPrefixes per response; now that this is the default
        # Files-page browse path on aws_hosted (SFBL-385), a single call would
        # silently truncate large prefixes. Loop until the listing is no longer
        # truncated. (Continuation-token loop rather than the paginator so the
        # bounded retry/observability stays inline.)
        continuation_token: Optional[str] = None
        try:
            while True:
                list_kwargs: dict = {
                    "Bucket": self._bucket,
                    "Prefix": prefix,
                    "Delimiter": "/",
                }
                if continuation_token:
                    list_kwargs["ContinuationToken"] = continuation_token
                response = self._client.list_objects_v2(**list_kwargs)

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

                if not response.get("IsTruncated"):
                    break
                continuation_token = response.get("NextContinuationToken")
                if not continuation_token:
                    break
        except botocore.exceptions.ClientError as exc:
            logger.warning(
                "S3 input list failed for s3://%s/%s: %s", self._bucket, prefix, exc,
                extra={
                    "event_name": StorageEvent.INPUT_FAILED,
                    "outcome_code": _s3_outcome_code(exc),
                    "s3_bucket": self._bucket,
                },
            )
            raise InputStorageError(f"Could not list S3 path {path!r}: {exc}") from exc

        return _sort_entries(entries)

    def preview_file(
        self,
        path: str,
        limit: int = 50,
        offset: int = 0,
        filters: list[dict[str, str]] | None = None,
        *,
        encoding: str | None = None,
    ) -> InputPreview:
        """Return a paginated, optionally filtered page of rows from an S3 CSV object.

        Uses :meth:`open_text` for streaming so the full object is never loaded
        into memory at once.  Decodes with ``errors="replace"`` so browsing
        never raises (D1.11) — preview is advisory, and these endpoints have no
        step on which an operator could set an encoding.

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
            with self.open_text(path, encoding=encoding, errors="replace") as fh:
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
            )

        with self.open_text(path, encoding=encoding, errors="replace") as fh:
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
            logger.warning(
                "S3 input discovery failed for %r in s3://%s: %s",
                glob_pattern, self._bucket, exc,
                extra={
                    "event_name": StorageEvent.INPUT_FAILED,
                    "outcome_code": _s3_outcome_code(exc),
                    "s3_bucket": self._bucket,
                },
            )
            raise InputStorageError(f"Could not discover S3 files for {glob_pattern!r}: {exc}") from exc

        return sorted(matched)

    def open_text(
        self, path: str, *, encoding: str | None = None, errors: str = "strict"
    ) -> IO[str]:
        """Open *path* for sequential text reading without loading the full object.

        Decodes with *encoding* (default UTF-8) and streams, so memory stays
        bounded regardless of object size.  There is no encoding detection.

        A ``reread`` closure is supplied to the decoding stream because the
        underlying :class:`_S3StreamingBodyReader` wraps an already-partially
        consumed ``StreamingBody`` and implements no ``seek`` — without it the
        post-failure diagnostic could not re-read the object at all.

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
            logger.warning(
                "S3 input read failed for s3://%s/%s: %s", self._bucket, key, exc,
                extra={
                    "event_name": StorageEvent.INPUT_FAILED,
                    "outcome_code": _s3_outcome_code(exc),
                    "s3_bucket": self._bucket,
                },
            )
            raise InputStorageError(
                f"Could not read S3 object {path!r}: {exc}"
            ) from exc

        body = response["Body"]
        raw = _S3StreamingBodyReader(body, b"")
        buffered = io.BufferedReader(raw, buffer_size=65536)

        def _reread() -> IO[bytes]:
            again = self._client.get_object(Bucket=self._bucket, Key=key)
            return again["Body"]

        return _DecodingTextStream(
            buffered,
            path=path,
            encoding=resolve_encoding(encoding),
            reread=_reread,
            errors=errors,
        )


LOCAL_OUTPUT_SOURCE = "local-output"
"""Sentinel value for :func:`get_storage` / :data:`LoadStep.input_connection_id`
that routes reads to :data:`settings.output_dir` instead of the input tree.

Used by SFBL-178 so DML steps can chain a prior run's output (e.g. query
results written by SFBL-114) into a subsequent run as an input source, via
the same :class:`BaseInputStorage` contract used for true inputs.
"""


def _s3_default_storage_enabled() -> bool:
    """Whether the implicit/default source resolves to first-party S3 (SFBL-385).

    True when ``input_storage_mode == "s3"`` (the aws_hosted default). The
    bucket coordinates are guaranteed present by the fail-fast startup
    validation in :class:`app.config.Settings`.
    """
    return settings.input_storage_mode == "s3"


def _default_input_storage() -> "S3InputStorage":
    """Keyless first-party-input-bucket storage for the default source."""
    logger.info(
        "Default input source resolves to s3://%s (keyless task-role)",
        settings.s3_input_bucket,
        extra={
            "event_name": StorageEvent.INPUT_LISTED,
            "outcome_code": OutcomeCode.OK,
            "s3_bucket": settings.s3_input_bucket,
        },
    )
    return S3InputStorage(
        bucket=settings.s3_input_bucket,
        root_prefix=settings.s3_input_prefix,
        region=settings.s3_bucket_region,
    )


def _default_output_as_input_storage() -> "S3InputStorage":
    """Keyless first-party-output-bucket storage, browsed as an input source.

    Backs the ``local-output`` sentinel on ``aws_hosted`` so the Files-page
    Output tab and DML steps chaining a prior run's output read from the
    first-party output bucket instead of the ephemeral container filesystem.
    """
    logger.info(
        "Default output-as-input source resolves to s3://%s (keyless task-role)",
        settings.s3_output_bucket,
        extra={
            "event_name": StorageEvent.INPUT_LISTED,
            "outcome_code": OutcomeCode.OK,
            "s3_bucket": settings.s3_output_bucket,
        },
    )
    return S3InputStorage(
        bucket=settings.s3_output_bucket,
        root_prefix=settings.s3_output_prefix,
        region=settings.s3_bucket_region,
    )


async def get_storage(source: Optional[str], db: AsyncSession) -> BaseInputStorage:
    """Resolve *source* to the appropriate input storage provider.

    On ``input_storage_mode == "s3"`` (aws_hosted) the implicit/default source
    (``None`` / ``""`` / ``"local"``) and the ``local-output`` sentinel resolve
    to the deployment's first-party S3 buckets via the keyless task-role chain
    (SFBL-385). On the filesystem profiles they resolve to the local input /
    output directories exactly as before. Explicit connection-id sources are
    unchanged on every profile (BYO keys, decrypted).
    """
    from app.services.settings.dirs import effective_input_dir, effective_output_dir  # noqa: PLC0415

    if source in (None, "", "local"):
        if _s3_default_storage_enabled():
            return _default_input_storage()
        return LocalInputStorage(await effective_input_dir())

    if source == LOCAL_OUTPUT_SOURCE:
        if _s3_default_storage_enabled():
            return _default_output_as_input_storage()
        return LocalInputStorage(await effective_output_dir())

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


# ── Storage-location metadata (SFBL-296) ───────────────────────────────────────


@dataclass
class StorageLocation:
    """Non-secret description of where the implicit Input/Output files live.

    Surfaced by ``/api/runtime`` so the Files page can tell operators *where*
    the listed files physically reside. Carries **no** credentials, tokens, or
    presigned URLs — only the deployment-identity coordinates (bucket / region /
    prefix) and a human-readable display URI/path.
    """

    provider: str           # "s3" | "local"
    uri: str                # "s3://bucket/prefix" or the filesystem directory
    bucket: Optional[str]   # S3 bucket name, or None for local
    region: Optional[str]   # S3 region, or None for local
    prefix: Optional[str]   # S3 key prefix, or None when unset / local


def _s3_display_uri(bucket: Optional[str], prefix: Optional[str]) -> str:
    norm = _normalise_root_prefix(prefix)
    return f"s3://{bucket}/{norm}" if norm else f"s3://{bucket}"


async def resolve_storage_locations() -> dict[str, StorageLocation]:
    """Return the resolved Input and Output storage locations (no secrets).

    Mirrors exactly what :func:`get_storage` / ``get_output_storage`` resolve
    for the default source on the active profile: first-party S3 buckets on
    ``input_storage_mode == "s3"``, otherwise the effective local directories.
    """
    from app.services.settings.dirs import effective_input_dir, effective_output_dir  # noqa: PLC0415

    if _s3_default_storage_enabled():
        return {
            "input": StorageLocation(
                provider="s3",
                uri=_s3_display_uri(settings.s3_input_bucket, settings.s3_input_prefix),
                bucket=settings.s3_input_bucket,
                region=settings.s3_bucket_region,
                prefix=settings.s3_input_prefix or None,
            ),
            "output": StorageLocation(
                provider="s3",
                uri=_s3_display_uri(settings.s3_output_bucket, settings.s3_output_prefix),
                bucket=settings.s3_output_bucket,
                region=settings.s3_bucket_region,
                prefix=settings.s3_output_prefix or None,
            ),
        }

    return {
        "input": StorageLocation(
            provider="local",
            uri=await effective_input_dir(),
            bucket=None,
            region=None,
            prefix=None,
        ),
        "output": StorageLocation(
            provider="local",
            uri=await effective_output_dir(),
            bucket=None,
            region=None,
            prefix=None,
        ),
    }
