"""CSV Processor (spec §4.3).

Responsibilities
----------------
- **File discovery**: resolve a glob pattern relative to the configured input
  directory and return matching file paths.
- **Validation**: read the header row and optionally compare it against a list
  of expected Salesforce field names.  Emits warnings but does not block.
- **Partitioning**: stream a CSV into fixed-size chunks, preserving the header
  row in every chunk.  At most one partition's worth of rows is held in memory
  at any time.
- **Encoding normalisation**: detect latin-1 / cp1252 / UTF-8 (± BOM) and
  re-emit every partition as UTF-8 with LF line endings (required by the
  Salesforce Bulk API 2.0, spec §10).

Security note
-------------
``discover_files`` rejects glob patterns containing the ``..`` path-traversal
sequence and validates that every resolved candidate path stays inside the
configured input directory (spec §11).
"""

from __future__ import annotations

import csv
import io
import logging
import pathlib
from dataclasses import dataclass, field
from typing import Iterator, Optional, Sequence

from app.config import settings

logger = logging.getLogger(__name__)

# Encodings attempted during detection, in priority order.
# ``utf-8-sig`` handles UTF-8 with and without BOM and is tried first.
# ``cp1252`` (Windows-1252) is tried before ``latin-1`` because it is the
# most common non-UTF-8 encoding in practice.
# ``latin-1`` is last because it accepts every byte value and never raises,
# making it the universal fallback.
_ENCODING_CANDIDATES: tuple[str, ...] = ("utf-8-sig", "cp1252", "latin-1")


# ── Exceptions ───────────────────────────────────────────────────────────────


class CSVProcessorError(Exception):
    """Raised for unrecoverable errors during CSV processing."""


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class CSVValidationResult:
    """Outcome of inspecting the headers of a single CSV file."""

    headers: list[str]
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """True when no warnings were raised."""
        return not self.warnings


# ── Encoding detection ────────────────────────────────────────────────────────


def detect_encoding(file_path: pathlib.Path, sample_size: int = 65536) -> str:
    """Return the most likely text encoding for *file_path*.

    Reads the first *sample_size* bytes (default 64 KiB) and tries each
    encoding in :data:`_ENCODING_CANDIDATES` until one succeeds without a
    :exc:`UnicodeDecodeError`.  ``latin-1`` always succeeds and acts as the
    universal fallback.

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


# ── File discovery ────────────────────────────────────────────────────────────


def discover_files(
    glob_pattern: str,
    input_dir: Optional[str] = None,
) -> list[pathlib.Path]:
    """Return CSV files inside *input_dir* that match *glob_pattern*.

    The pattern is evaluated via :meth:`pathlib.Path.glob` relative to
    *input_dir*.  Patterns containing ``..`` are rejected before any filesystem
    access to prevent path traversal (spec §11).  Every matched candidate is
    also validated to ensure its resolved path stays inside *input_dir*.

    Args:
        glob_pattern: Glob pattern relative to *input_dir*
            (e.g. ``"accounts_*.csv"`` or ``"subdir/**/*.csv"``).
        input_dir: Root directory to search.  Defaults to
            ``settings.input_dir``.

    Returns:
        Sorted list of :class:`pathlib.Path` objects for regular files only.

    Raises:
        CSVProcessorError: If *glob_pattern* contains the ``..``
            path-traversal sequence.
    """
    # Normalise to forward-slash parts for the traversal check.
    normalised = glob_pattern.replace("\\", "/")
    if any(part == ".." for part in normalised.split("/")):
        raise CSVProcessorError(
            f"Glob pattern {glob_pattern!r} contains path traversal sequence '..'"
        )

    base = pathlib.Path(input_dir or settings.input_dir).resolve()
    matched: list[pathlib.Path] = []

    for candidate in sorted(base.glob(glob_pattern)):
        if not candidate.is_file():
            continue
        # Belt-and-suspenders: confirm resolved path remains inside base.
        try:
            candidate.resolve().relative_to(base)
        except ValueError:
            logger.warning(
                "Skipping %s: resolved path escapes the input directory", candidate
            )
            continue
        matched.append(candidate)

    logger.info(
        "discover_files: pattern=%r dir=%s matched %d file(s)",
        glob_pattern,
        base,
        len(matched),
    )
    return matched


# ── Header validation ─────────────────────────────────────────────────────────


def validate_csv_headers(
    file_path: pathlib.Path,
    expected_fields: Optional[Sequence[str]] = None,
    *,
    encoding: Optional[str] = None,
) -> CSVValidationResult:
    """Inspect the header row of *file_path* and optionally compare to expected fields.

    Validation is advisory: mismatches produce warnings but never raise.
    Leading/trailing whitespace is stripped from all header names.

    Args:
        file_path: Path to the CSV file.
        expected_fields: Optional sequence of expected Salesforce field names.
            Warns on missing fields and on unexpected extra fields.
        encoding: Source file encoding.  Auto-detected when ``None``.

    Returns:
        :class:`CSVValidationResult` containing the headers and any warnings.

    Raises:
        CSVProcessorError: If the file contains no header row (completely
            empty or unreadable).
    """
    enc = encoding or detect_encoding(file_path)
    warnings: list[str] = []

    try:
        with file_path.open(encoding=enc, newline="") as fh:
            reader = csv.reader(fh)
            try:
                raw_headers = next(reader)
            except StopIteration:
                raise CSVProcessorError(
                    f"File '{file_path.name}' is empty — no header row found."
                )
    except UnicodeDecodeError as exc:
        raise CSVProcessorError(
            f"Could not decode '{file_path.name}' with encoding '{enc}': {exc}"
        ) from exc

    headers = [h.strip() for h in raw_headers]

    if expected_fields is not None:
        expected_set = set(expected_fields)
        actual_set = set(headers)
        missing = sorted(expected_set - actual_set)
        extra = sorted(actual_set - expected_set)
        if missing:
            msg = f"Missing expected field(s): {missing}"
            warnings.append(msg)
            logger.warning("%s: %s", file_path.name, msg)
        if extra:
            msg = f"Extra field(s) not in expected list: {extra}"
            warnings.append(msg)
            logger.warning("%s: %s", file_path.name, msg)

    return CSVValidationResult(headers=headers, warnings=warnings)


# ── Partitioning ──────────────────────────────────────────────────────────────


def partition_csv(
    file_path: pathlib.Path,
    partition_size: int,
    *,
    encoding: Optional[str] = None,
) -> Iterator[bytes]:
    """Stream-partition *file_path* into fixed-size CSV chunks.

    Each yielded value is a complete, self-contained CSV: the original header
    row followed by up to *partition_size* data rows.  Only one partition's
    worth of rows is kept in memory at once; the source file is read
    sequentially via the standard :mod:`csv` module.

    Encoding is normalised to **UTF-8** and line endings to **LF** in every
    output partition, as required by the Salesforce Bulk API 2.0 (spec §10).

    Args:
        file_path: Path to the source CSV file.
        partition_size: Maximum number of data rows per partition.  Must be ≥ 1.
        encoding: Source-file encoding.  Auto-detected when ``None``.

    Yields:
        UTF-8-encoded CSV bytes for each partition, in source-file order.
        Yields nothing if the file contains only a header row (no data rows).

    Raises:
        CSVProcessorError: If *partition_size* < 1 or the file has no header
            row (completely empty).
    """
    if partition_size < 1:
        raise CSVProcessorError(
            f"partition_size must be ≥ 1, got {partition_size!r}"
        )

    enc = encoding or detect_encoding(file_path)

    with file_path.open(encoding=enc, newline="") as fh:
        reader = csv.reader(fh)

        try:
            raw_headers = next(reader)
        except StopIteration:
            raise CSVProcessorError(
                f"File '{file_path.name}' is empty — no header row found."
            )

        header: list[str] = [h.strip() for h in raw_headers]
        buf: list[list[str]] = []

        for row in reader:
            buf.append(row)
            if len(buf) == partition_size:
                yield _render_partition(header, buf)
                buf = []

        # Emit the final partial partition (skipped when buf is empty, i.e.
        # the file contained only a header row).
        if buf:
            yield _render_partition(header, buf)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _render_partition(header: list[str], rows: list[list[str]]) -> bytes:
    """Serialise *header* + *rows* as UTF-8 bytes with LF line endings."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")
