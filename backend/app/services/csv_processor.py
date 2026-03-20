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
from app.services.input_storage import InputStorageError, LocalInputStorage, detect_encoding  # noqa: F401 — re-export

logger = logging.getLogger(__name__)


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


# ── File discovery ────────────────────────────────────────────────────────────


def discover_files(
    glob_pattern: str,
    input_dir: Optional[str] = None,
) -> list[pathlib.Path]:
    """Return CSV files inside *input_dir* that match *glob_pattern*.

    Delegates to :class:`~app.services.input_storage.LocalInputStorage` which
    is the single source of truth for traversal-safe file discovery.

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
    storage = LocalInputStorage(input_dir or settings.input_dir)
    try:
        base = pathlib.Path(input_dir or settings.input_dir).resolve()
        return [base / rel_path for rel_path in storage.discover_files(glob_pattern)]
    except InputStorageError as exc:
        raise CSVProcessorError(str(exc)) from exc


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


# ── Retry partition builder ───────────────────────────────────────────────────


def build_retry_partitions(
    job_records: list,  # list[JobRecord] — typed as list to avoid circular import
    step: object,  # LoadStep
    partition_size: int,
    output_dir: str,
) -> list[bytes]:
    """Build CSV partition bytes for retrying failed/aborted jobs of a single step.

    Jobs fall into two tracks:

    **Track A** — job has result files (reached Salesforce):
    - ``error_file_path``: read, strip ``sf__Id``/``sf__Error`` columns (first two),
      collect remaining data rows.
    - ``unprocessed_file_path``: read as-is, collect data rows.
    - All Track A rows are pooled then re-partitioned at *partition_size*.

    **Track B** — no result files (job never reached Salesforce; stuck in pending/uploading):
    - The original CSV partition is re-discovered via the step's glob pattern and
      ``partition_index``, then yielded as-is.

    Args:
        job_records: Failed/aborted :class:`JobRecord` instances for the step.
        step: The :class:`LoadStep` that owns these jobs.
        partition_size: Maximum data rows per output partition.
        output_dir: Absolute path to the output directory (``settings.output_dir``).

    Returns:
        List of UTF-8 CSV bytes: Track A re-partitioned chunks followed by
        Track B original chunks.  Returns ``[]`` if there are no retryable records.
    """
    output_base = pathlib.Path(output_dir)

    track_a_header: list[str] | None = None
    track_a_rows: list[list[str]] = []
    track_b_chunks: list[bytes] = []

    for job in job_records:
        has_result_files = job.error_file_path or job.unprocessed_file_path

        if has_result_files:
            # ── Track A ──────────────────────────────────────────────────────
            if job.error_file_path:
                error_path = output_base / job.error_file_path
                if error_path.is_file():
                    enc = detect_encoding(error_path)
                    try:
                        with error_path.open(encoding=enc, newline="") as fh:
                            reader = csv.reader(fh)
                            raw_header = next(reader, None)
                            if raw_header is not None:
                                # Strip sf__Id and sf__Error (always first two cols)
                                data_header = [h.strip() for h in raw_header[2:]]
                                if track_a_header is None:
                                    track_a_header = data_header
                                for row in reader:
                                    track_a_rows.append(row[2:])
                    except Exception:
                        logger.warning(
                            "build_retry_partitions: could not read error file %s",
                            error_path,
                        )

            if job.unprocessed_file_path:
                unprocessed_path = output_base / job.unprocessed_file_path
                if unprocessed_path.is_file():
                    enc = detect_encoding(unprocessed_path)
                    try:
                        with unprocessed_path.open(encoding=enc, newline="") as fh:
                            reader = csv.reader(fh)
                            raw_header = next(reader, None)
                            if raw_header is not None:
                                if track_a_header is None:
                                    track_a_header = [h.strip() for h in raw_header]
                                for row in reader:
                                    track_a_rows.append(row)
                    except Exception:
                        logger.warning(
                            "build_retry_partitions: could not read unprocessed file %s",
                            unprocessed_path,
                        )
        else:
            # ── Track B ──────────────────────────────────────────────────────
            target_idx: int = job.partition_index
            current_idx = 0
            csv_files = discover_files(step.csv_file_pattern)
            found = False
            for csv_file in csv_files:
                for chunk in partition_csv(csv_file, step.partition_size):
                    if current_idx == target_idx:
                        track_b_chunks.append(chunk)
                        found = True
                        break
                    current_idx += 1
                if found:
                    break
            if not found:
                logger.warning(
                    "build_retry_partitions: could not locate original partition %d "
                    "for step pattern %r",
                    target_idx,
                    step.csv_file_pattern,
                )

    # Re-partition pooled Track A rows
    track_a_chunks: list[bytes] = []
    if track_a_rows and track_a_header is not None:
        for i in range(0, len(track_a_rows), partition_size):
            chunk_rows = track_a_rows[i : i + partition_size]
            track_a_chunks.append(_render_partition(track_a_header, chunk_rows))

    return track_a_chunks + track_b_chunks


# ── Internal helpers ──────────────────────────────────────────────────────────


def _render_partition(header: list[str], rows: list[list[str]]) -> bytes:
    """Serialise *header* + *rows* as UTF-8 bytes with LF line endings."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")
