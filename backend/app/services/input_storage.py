"""Input storage service — single source of truth for local file operations.

Centralises path-safety validation, directory listing, CSV preview, row counting,
encoding detection, and glob-pattern discovery.  All file-browsing consumers
(the files API, step preview) delegate here rather than implementing their own.

Designed to match the storage abstraction interface in ``input-storage-spec.md``
so that a remote provider (e.g. ``S3InputStorage``) can be added alongside
``LocalInputStorage`` without rewiring callers.
"""

from __future__ import annotations

import csv
import logging
import os
import pathlib
from dataclasses import dataclass
from typing import IO, Optional

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
    """First N rows of a CSV file returned by :meth:`LocalInputStorage.preview_file`."""

    filename: str
    header: list[str]
    rows: list[dict]
    row_count: int  # number of preview rows returned (≤ requested limit)


# ── Encoding detection ────────────────────────────────────────────────────────


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
    raw = file_path.read_bytes()[:sample_size]
    for enc in _ENCODING_CANDIDATES:
        try:
            raw.decode(enc)
            logger.debug("Detected encoding %s for %s", enc, file_path.name)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return "latin-1"  # pragma: no cover — latin-1 never raises


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
        normalised = rel_path.replace("\\", "/")
        parts = pathlib.PurePosixPath(normalised).parts
        if ".." in parts:
            return None
        candidate = (self._base / rel_path).resolve()
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

        dirs: list[InputEntry] = []
        files: list[InputEntry] = []

        try:
            with os.scandir(target) as it:
                for entry in sorted(it, key=lambda e: e.name):
                    if entry.name.startswith("."):
                        continue
                    rel = os.path.join(path, entry.name) if path else entry.name
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append(
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
                        files.append(
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

        return dirs + files

    def preview_file(self, path: str, rows: int) -> InputPreview:
        """Return the first *rows* data rows (plus header) of a CSV file.

        Uses :func:`detect_encoding` so files encoded as cp1252 or latin-1 are
        handled correctly (unlike the previous hard-coded ``utf-8-sig`` approach).

        Args:
            path: Relative path to the CSV file.
            rows: Maximum number of data rows to return.

        Returns:
            :class:`InputPreview` with header, preview rows, and row count.

        Raises:
            :exc:`InputStorageError`: If *path* is invalid or attempts traversal.
            :exc:`FileNotFoundError`: If *path* does not exist or is not a file.
            :exc:`OSError`: If the file cannot be read.
        """
        resolved = self._safe_path(path)
        if resolved is None:
            raise InputStorageError(f"Invalid path: {path!r}")
        if not resolved.is_file():
            raise FileNotFoundError(f"File not found: {path!r}")

        enc = detect_encoding(resolved)
        with open(resolved, newline="", encoding=enc) as fh:
            reader = csv.DictReader(fh)
            header = list(reader.fieldnames or [])
            preview_rows = [dict(row) for _, row in zip(range(rows), reader)]

        return InputPreview(
            filename=path,
            header=header,
            rows=preview_rows,
            row_count=len(preview_rows),
        )

    def discover_files(self, glob_pattern: str) -> list[pathlib.Path]:
        """Return files inside the base directory that match *glob_pattern*.

        Two-layer traversal protection is applied:

        1. Patterns containing ``".."`` are rejected before any filesystem access.
        2. Every matched candidate is validated to ensure its resolved path stays
           inside the base directory.

        Args:
            glob_pattern: Glob pattern relative to the base directory
                (e.g. ``"accounts_*.csv"`` or ``"subdir/**/*.csv"``).

        Returns:
            Sorted list of :class:`~pathlib.Path` objects for regular files only.

        Raises:
            :exc:`InputStorageError`: If *glob_pattern* contains ``".."``.
        """
        normalised = glob_pattern.replace("\\", "/")
        if any(part == ".." for part in normalised.split("/")):
            raise InputStorageError(
                f"Pattern {glob_pattern!r} contains path traversal sequence '..'"
            )

        matched: list[pathlib.Path] = []
        for candidate in sorted(self._base.glob(glob_pattern)):
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
            matched.append(candidate)

        logger.info(
            "discover_files: pattern=%r dir=%s matched %d file(s)",
            glob_pattern,
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
