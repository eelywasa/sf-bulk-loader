"""Tests for app.services.csv_processor (spec §4.3).

Edge cases explicitly called out in the spec (§12.1):
  - Empty files (0 bytes, and header-only)
  - Single record
  - Exact partition boundary
  - Unicode content

Additional coverage:
  - File discovery: glob matching, sorted results, path-traversal rejection,
    directory entries skipped.
  - Encoding detection: UTF-8, UTF-8 BOM, latin-1, cp1252.
  - Header validation: missing/extra fields, whitespace stripping.
  - Partitioning: multiple partitions, LF line endings, latin-1 → UTF-8
    normalisation, invalid partition_size.
"""

from __future__ import annotations

import io
import pathlib
from typing import Iterator

import pytest

from app.services.csv_processor import (
    CSVProcessorError,
    CSVValidationResult,
    _render_partition,
    detect_encoding,
    discover_files,
    partition_csv,
    validate_csv_headers,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def write_csv(
    path: pathlib.Path,
    content: str,
    encoding: str = "utf-8",
) -> pathlib.Path:
    """Write *content* encoded as *encoding* to *path* and return it."""
    path.write_bytes(content.encode(encoding))
    return path


def collect(it: Iterator[bytes]) -> list[bytes]:
    """Materialise a bytes iterator into a list."""
    return list(it)


def decode_partition(data: bytes) -> list[list[str]]:
    """Parse a UTF-8 CSV partition into a list of rows (each row is a list of cells)."""
    import csv
    import io

    reader = csv.reader(io.StringIO(data.decode("utf-8")))
    return list(reader)


# ── detect_encoding ───────────────────────────────────────────────────────────


class TestDetectEncoding:
    def test_utf8_file(self, tmp_path: pathlib.Path) -> None:
        f = write_csv(tmp_path / "a.csv", "Name,Age\nAlice,30\n", "utf-8")
        # utf-8-sig succeeds on plain UTF-8 (BOM is optional)
        assert detect_encoding(f) == "utf-8-sig"

    def test_utf8_bom_file(self, tmp_path: pathlib.Path) -> None:
        # Write UTF-8 with BOM explicitly
        content = "Name,Age\nAlice,30\n"
        bom_bytes = b"\xef\xbb\xbf" + content.encode("utf-8")
        f = tmp_path / "bom.csv"
        f.write_bytes(bom_bytes)
        assert detect_encoding(f) == "utf-8-sig"

    def test_latin1_file(self, tmp_path: pathlib.Path) -> None:
        # Bytes 0x81, 0x8D, 0x8F, 0x90, 0x9D are undefined in cp1252 but are
        # valid C1 control characters in latin-1.  Using 0x81 guarantees that
        # cp1252 decoding raises before latin-1 is tried.
        f = tmp_path / "latin1.csv"
        f.write_bytes(b"Name\n\x81test\n")
        assert detect_encoding(f) == "latin-1"

    def test_cp1252_file(self, tmp_path: pathlib.Path) -> None:
        # Byte 0x80 maps to the Euro sign (€, U+20AC) in cp1252.  It is NOT
        # valid UTF-8 and IS valid in cp1252 — write the raw byte directly
        # rather than encoding a Python string (U+0080 ≠ €).
        f = tmp_path / "cp1252.csv"
        f.write_bytes(b"Price\n\x80100\n")
        assert detect_encoding(f) == "cp1252"

    def test_sample_size_respected(self, tmp_path: pathlib.Path) -> None:
        # Place invalid-UTF-8 bytes only beyond the sample window.
        # With a large sample_size the detection should still find utf-8-sig.
        valid_prefix = "Name\n" + "A" * 100 + "\n"
        f = tmp_path / "bigfile.csv"
        f.write_bytes(valid_prefix.encode("utf-8"))
        # sample_size larger than file → no problem
        assert detect_encoding(f, sample_size=65536) == "utf-8-sig"


# ── discover_files ────────────────────────────────────────────────────────────


class TestDiscoverFiles:
    def test_basic_glob_match(self, tmp_path: pathlib.Path) -> None:
        write_csv(tmp_path / "accounts_1.csv", "Id,Name\n1,Acme\n")
        write_csv(tmp_path / "accounts_2.csv", "Id,Name\n2,Beta\n")
        write_csv(tmp_path / "contacts.csv", "Id,Email\n1,a@b.com\n")

        result = discover_files("accounts_*.csv", str(tmp_path))

        assert len(result) == 2
        names = {p.name for p in result}
        assert names == {"accounts_1.csv", "accounts_2.csv"}

    def test_no_matches_returns_empty_list(self, tmp_path: pathlib.Path) -> None:
        result = discover_files("nonexistent_*.csv", str(tmp_path))
        assert result == []

    def test_results_are_sorted(self, tmp_path: pathlib.Path) -> None:
        write_csv(tmp_path / "c.csv", "H\n1\n")
        write_csv(tmp_path / "a.csv", "H\n2\n")
        write_csv(tmp_path / "b.csv", "H\n3\n")

        result = discover_files("*.csv", str(tmp_path))

        assert [p.name for p in result] == ["a.csv", "b.csv", "c.csv"]

    def test_directories_not_returned(self, tmp_path: pathlib.Path) -> None:
        # Create a directory whose name matches the glob
        (tmp_path / "dir.csv").mkdir()
        write_csv(tmp_path / "real.csv", "H\n1\n")

        result = discover_files("*.csv", str(tmp_path))

        assert len(result) == 1
        assert result[0].name == "real.csv"

    def test_path_traversal_double_dot_raises(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(CSVProcessorError, match="path traversal"):
            discover_files("../other/*.csv", str(tmp_path))

    def test_path_traversal_embedded_raises(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(CSVProcessorError, match="path traversal"):
            discover_files("subdir/../../etc/*.csv", str(tmp_path))

    def test_wildcard_without_traversal_is_fine(self, tmp_path: pathlib.Path) -> None:
        subdir = tmp_path / "sub"
        subdir.mkdir()
        write_csv(subdir / "x.csv", "H\n1\n")

        result = discover_files("sub/*.csv", str(tmp_path))

        assert len(result) == 1

    def test_recursive_glob_match(self, tmp_path: pathlib.Path) -> None:
        sub = tmp_path / "deep" / "nested"
        sub.mkdir(parents=True)
        write_csv(sub / "data.csv", "H\n1\n")

        result = discover_files("**/*.csv", str(tmp_path))

        assert any(p.name == "data.csv" for p in result)


# ── validate_csv_headers ──────────────────────────────────────────────────────


class TestValidateCSVHeaders:
    def test_empty_file_raises(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "empty.csv"
        f.write_bytes(b"")

        with pytest.raises(CSVProcessorError, match="empty"):
            validate_csv_headers(f)

    def test_returns_correct_headers(self, tmp_path: pathlib.Path) -> None:
        f = write_csv(tmp_path / "a.csv", "Name,Email,Phone\nAlice,a@b.com,555\n")

        result = validate_csv_headers(f)

        assert result.headers == ["Name", "Email", "Phone"]

    def test_strips_whitespace_from_headers(self, tmp_path: pathlib.Path) -> None:
        f = write_csv(tmp_path / "a.csv", " Name , Email \nAlice,a@b.com\n")

        result = validate_csv_headers(f)

        assert result.headers == ["Name", "Email"]

    def test_no_expected_fields_no_warnings(self, tmp_path: pathlib.Path) -> None:
        f = write_csv(tmp_path / "a.csv", "Name,Email\nAlice,a@b.com\n")

        result = validate_csv_headers(f)

        assert result.warnings == []
        assert result.is_valid

    def test_exact_match_no_warnings(self, tmp_path: pathlib.Path) -> None:
        f = write_csv(tmp_path / "a.csv", "Name,Email\nAlice,a@b.com\n")

        result = validate_csv_headers(f, expected_fields=["Name", "Email"])

        assert result.warnings == []
        assert result.is_valid

    def test_missing_field_produces_warning(self, tmp_path: pathlib.Path) -> None:
        f = write_csv(tmp_path / "a.csv", "Name\nAlice\n")

        result = validate_csv_headers(f, expected_fields=["Name", "Email"])

        assert not result.is_valid
        assert any("Missing" in w for w in result.warnings)
        assert any("Email" in w for w in result.warnings)

    def test_extra_field_produces_warning(self, tmp_path: pathlib.Path) -> None:
        f = write_csv(tmp_path / "a.csv", "Name,Email,Phone\nAlice,a@b.com,555\n")

        result = validate_csv_headers(f, expected_fields=["Name", "Email"])

        assert not result.is_valid
        assert any("Extra" in w for w in result.warnings)
        assert any("Phone" in w for w in result.warnings)

    def test_missing_and_extra_both_warned(self, tmp_path: pathlib.Path) -> None:
        f = write_csv(tmp_path / "a.csv", "Name,Phone\nAlice,555\n")

        result = validate_csv_headers(f, expected_fields=["Name", "Email"])

        assert len(result.warnings) == 2

    def test_header_only_file_is_valid(self, tmp_path: pathlib.Path) -> None:
        # Header row exists, but no data rows — validation still succeeds.
        f = write_csv(tmp_path / "a.csv", "Name,Email\n")

        result = validate_csv_headers(f, expected_fields=["Name", "Email"])

        assert result.is_valid

    def test_latin1_encoding_detected_and_read(self, tmp_path: pathlib.Path) -> None:
        # Header contains a latin-1 character — should be decoded without error.
        header = "Pr\xe9nom,Nom\n"  # Prénom,Nom in latin-1
        f = tmp_path / "latin1.csv"
        f.write_bytes(header.encode("latin-1"))

        result = validate_csv_headers(f)

        assert "Prénom" in result.headers


# ── partition_csv ─────────────────────────────────────────────────────────────


class TestPartitionCSV:
    # ── Error conditions ─────────────────────────────────────────────────────

    def test_zero_partition_size_raises(self, tmp_path: pathlib.Path) -> None:
        f = write_csv(tmp_path / "a.csv", "Name\nAlice\n")

        with pytest.raises(CSVProcessorError, match="partition_size"):
            collect(partition_csv(f, 0))

    def test_negative_partition_size_raises(self, tmp_path: pathlib.Path) -> None:
        f = write_csv(tmp_path / "a.csv", "Name\nAlice\n")

        with pytest.raises(CSVProcessorError, match="partition_size"):
            collect(partition_csv(f, -5))

    def test_empty_file_raises(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "empty.csv"
        f.write_bytes(b"")

        with pytest.raises(CSVProcessorError, match="empty"):
            collect(partition_csv(f, 10))

    # ── Zero-data-row files ──────────────────────────────────────────────────

    def test_header_only_yields_nothing(self, tmp_path: pathlib.Path) -> None:
        """A file with only a header row produces zero partitions."""
        f = write_csv(tmp_path / "a.csv", "Name,Email\n")

        partitions = collect(partition_csv(f, 10))

        assert partitions == []

    # ── Single record ────────────────────────────────────────────────────────

    def test_single_record_one_partition(self, tmp_path: pathlib.Path) -> None:
        f = write_csv(tmp_path / "a.csv", "Name,Email\nAlice,alice@example.com\n")

        partitions = collect(partition_csv(f, 10))

        assert len(partitions) == 1
        rows = decode_partition(partitions[0])
        assert rows[0] == ["Name", "Email"]
        assert rows[1] == ["Alice", "alice@example.com"]

    def test_single_record_has_header(self, tmp_path: pathlib.Path) -> None:
        f = write_csv(tmp_path / "a.csv", "Id\n1\n")

        rows = decode_partition(collect(partition_csv(f, 100))[0])

        assert rows[0] == ["Id"]

    # ── Exact partition boundary ─────────────────────────────────────────────

    def test_exact_boundary_one_partition(self, tmp_path: pathlib.Path) -> None:
        """Exactly partition_size records → exactly one partition."""
        partition_size = 5
        lines = ["Name"] + [f"Row{i}" for i in range(partition_size)]
        f = write_csv(tmp_path / "a.csv", "\n".join(lines) + "\n")

        partitions = collect(partition_csv(f, partition_size))

        assert len(partitions) == 1
        rows = decode_partition(partitions[0])
        # header + 5 data rows = 6 rows total
        assert len(rows) == partition_size + 1

    def test_one_over_boundary_two_partitions(self, tmp_path: pathlib.Path) -> None:
        """partition_size + 1 records → two partitions."""
        partition_size = 3
        lines = ["Name"] + [f"Row{i}" for i in range(partition_size + 1)]
        f = write_csv(tmp_path / "a.csv", "\n".join(lines) + "\n")

        partitions = collect(partition_csv(f, partition_size))

        assert len(partitions) == 2

    # ── Multiple partitions ──────────────────────────────────────────────────

    def test_multiple_partitions_row_counts(self, tmp_path: pathlib.Path) -> None:
        """7 records, partition_size=3 → partitions of [3, 3, 1]."""
        lines = ["Name"] + [f"Row{i}" for i in range(7)]
        f = write_csv(tmp_path / "a.csv", "\n".join(lines) + "\n")

        partitions = collect(partition_csv(f, 3))

        assert len(partitions) == 3
        # Subtract 1 from each row count for the header row
        data_rows = [len(decode_partition(p)) - 1 for p in partitions]
        assert data_rows == [3, 3, 1]

    def test_headers_preserved_in_every_partition(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Every partition starts with the original header row."""
        lines = ["Id,Name"] + [f"{i},Row{i}" for i in range(10)]
        f = write_csv(tmp_path / "a.csv", "\n".join(lines) + "\n")

        partitions = collect(partition_csv(f, 3))

        for p in partitions:
            rows = decode_partition(p)
            assert rows[0] == ["Id", "Name"], f"Header missing in partition: {rows}"

    def test_all_data_rows_present_across_partitions(
        self, tmp_path: pathlib.Path
    ) -> None:
        """No records are dropped or duplicated when spanning partitions."""
        data_rows = [f"Row{i}" for i in range(11)]
        lines = ["Name"] + data_rows
        f = write_csv(tmp_path / "a.csv", "\n".join(lines) + "\n")

        partitions = collect(partition_csv(f, 4))

        collected: list[str] = []
        for p in partitions:
            rows = decode_partition(p)
            collected.extend(r[0] for r in rows[1:])  # skip header

        assert collected == data_rows

    # ── Output format ────────────────────────────────────────────────────────

    def test_output_is_utf8_decodable(self, tmp_path: pathlib.Path) -> None:
        f = write_csv(tmp_path / "a.csv", "Name\nAlice\n")

        raw = collect(partition_csv(f, 10))[0]

        # Must not raise
        decoded = raw.decode("utf-8")
        assert "Alice" in decoded

    def test_lf_line_endings(self, tmp_path: pathlib.Path) -> None:
        """Salesforce requires LF (\\n) line endings, not CRLF (\\r\\n)."""
        f = write_csv(tmp_path / "a.csv", "Name,Age\nAlice,30\nBob,25\n")

        raw = collect(partition_csv(f, 10))[0]

        assert b"\r\n" not in raw
        assert b"\n" in raw

    def test_crlf_input_normalised_to_lf(self, tmp_path: pathlib.Path) -> None:
        """CRLF line endings in the source file are normalised to LF in output."""
        crlf_content = b"Name,Age\r\nAlice,30\r\nBob,25\r\n"
        f = tmp_path / "crlf.csv"
        f.write_bytes(crlf_content)

        raw = collect(partition_csv(f, 10))[0]

        assert b"\r\n" not in raw

    # ── Unicode content ──────────────────────────────────────────────────────

    def test_unicode_content_utf8_output(self, tmp_path: pathlib.Path) -> None:
        """Unicode characters round-trip through UTF-8 encoding correctly."""
        content = "Name,City\n日本語,東京\nCafé,Montréal\n"
        f = write_csv(tmp_path / "unicode.csv", content, "utf-8")

        raw = collect(partition_csv(f, 10))[0]
        rows = decode_partition(raw)

        assert rows[1] == ["日本語", "東京"]
        assert rows[2] == ["Café", "Montréal"]

    def test_unicode_in_header(self, tmp_path: pathlib.Path) -> None:
        content = "Prénom,Nom\nAlice,Martin\n"
        f = write_csv(tmp_path / "unicode_header.csv", content, "utf-8")

        raw = collect(partition_csv(f, 10))[0]
        rows = decode_partition(raw)

        assert rows[0] == ["Prénom", "Nom"]

    # ── Encoding normalisation ────────────────────────────────────────────────

    def test_latin1_input_produces_utf8_output(self, tmp_path: pathlib.Path) -> None:
        """latin-1 source file is re-emitted as valid UTF-8."""
        # é is 0xE9 in latin-1
        content = "Name\nJos\xe9\n"
        f = tmp_path / "latin1.csv"
        f.write_bytes(content.encode("latin-1"))

        raw = collect(partition_csv(f, 10))[0]

        # Output must decode cleanly as UTF-8
        text = raw.decode("utf-8")
        assert "José" in text

    def test_utf8_bom_stripped_in_output(self, tmp_path: pathlib.Path) -> None:
        """UTF-8 BOM in the source file must not appear in output partitions."""
        bom_bytes = b"\xef\xbb\xbf" + "Name\nAlice\n".encode("utf-8")
        f = tmp_path / "bom.csv"
        f.write_bytes(bom_bytes)

        raw = collect(partition_csv(f, 10))[0]

        assert not raw.startswith(b"\xef\xbb\xbf")
        assert raw.decode("utf-8").startswith("Name")

    def test_cp1252_input_produces_utf8_output(self, tmp_path: pathlib.Path) -> None:
        """cp1252 source file (e.g. containing €) is re-emitted as UTF-8."""
        # Byte 0x80 = € in cp1252; write the raw byte directly.
        f = tmp_path / "cp1252.csv"
        f.write_bytes(b"Price\n\x80100\n")

        raw = collect(partition_csv(f, 10))[0]

        text = raw.decode("utf-8")
        assert "€" in text

    # ── Explicit encoding override ────────────────────────────────────────────

    def test_encoding_override_respected(self, tmp_path: pathlib.Path) -> None:
        """Callers can bypass auto-detection by supplying an encoding."""
        content = "Name\nJos\xe9\n"
        f = tmp_path / "latin1.csv"
        f.write_bytes(content.encode("latin-1"))

        raw = collect(partition_csv(f, 10, encoding="latin-1"))[0]

        assert "José" in raw.decode("utf-8")

    # ── Header whitespace stripping ───────────────────────────────────────────

    def test_header_whitespace_stripped_in_partitions(
        self, tmp_path: pathlib.Path
    ) -> None:
        f = write_csv(tmp_path / "a.csv", " Name , Email \nAlice,a@b.com\n")

        rows = decode_partition(collect(partition_csv(f, 10))[0])

        assert rows[0] == ["Name", "Email"]

    # ── Quoted fields ─────────────────────────────────────────────────────────

    def test_quoted_fields_with_commas(self, tmp_path: pathlib.Path) -> None:
        """Fields containing commas are quoted and round-trip correctly."""
        content = 'Name,Address\nAlice,"123 Main St, Suite 4"\n'
        f = write_csv(tmp_path / "a.csv", content)

        rows = decode_partition(collect(partition_csv(f, 10))[0])

        assert rows[1] == ["Alice", "123 Main St, Suite 4"]

    def test_quoted_fields_with_newlines(self, tmp_path: pathlib.Path) -> None:
        """Fields containing embedded newlines are handled by the CSV parser."""
        content = 'Notes\n"line1\nline2"\n'
        f = write_csv(tmp_path / "a.csv", content)

        rows = decode_partition(collect(partition_csv(f, 10))[0])

        # The embedded newline stays inside the cell
        assert rows[1] == ["line1\nline2"]


# ── partition_csv — IO[str] stream input ─────────────────────────────────────


class TestPartitionCSVStream:
    """partition_csv should accept an open IO[str] handle, not only pathlib.Path."""

    def test_accepts_text_stream(self) -> None:
        fh = io.StringIO("Name,Email\nAlice,alice@example.com\n")
        partitions = collect(partition_csv(fh, 10))
        assert len(partitions) == 1
        rows = decode_partition(partitions[0])
        assert rows[0] == ["Name", "Email"]
        assert rows[1] == ["Alice", "alice@example.com"]

    def test_stream_header_only_yields_nothing(self) -> None:
        fh = io.StringIO("Name,Email\n")
        assert collect(partition_csv(fh, 10)) == []

    def test_stream_empty_raises(self) -> None:
        fh = io.StringIO("")
        with pytest.raises(CSVProcessorError, match="empty"):
            collect(partition_csv(fh, 10))

    def test_stream_multiple_partitions(self) -> None:
        lines = ["Name"] + [f"Row{i}" for i in range(7)]
        fh = io.StringIO("\n".join(lines) + "\n")
        partitions = collect(partition_csv(fh, 3))
        assert len(partitions) == 3
        data_rows = [len(decode_partition(p)) - 1 for p in partitions]
        assert data_rows == [3, 3, 1]

    def test_stream_headers_preserved_in_all_partitions(self) -> None:
        lines = ["Id,Name"] + [f"{i},Row{i}" for i in range(10)]
        fh = io.StringIO("\n".join(lines) + "\n")
        for p in collect(partition_csv(fh, 3)):
            assert decode_partition(p)[0] == ["Id", "Name"]

    def test_stream_output_is_utf8_lf(self) -> None:
        fh = io.StringIO("Name,City\nAlice,London\n")
        raw = collect(partition_csv(fh, 10))[0]
        raw.decode("utf-8")  # must not raise
        assert b"\r\n" not in raw
        assert b"\n" in raw

    def test_pathlib_path_regression(self, tmp_path: pathlib.Path) -> None:
        """Existing pathlib.Path callers must be unaffected."""
        f = write_csv(tmp_path / "a.csv", "Id\n1\n2\n")
        partitions = collect(partition_csv(f, 10))
        assert len(partitions) == 1


# ── _render_partition (internal) ─────────────────────────────────────────────


class TestRenderPartition:
    def test_basic_output(self) -> None:
        raw = _render_partition(["Id", "Name"], [["1", "Alice"], ["2", "Bob"]])
        assert raw == b"Id,Name\n1,Alice\n2,Bob\n"

    def test_empty_rows_emits_header_only(self) -> None:
        raw = _render_partition(["Id", "Name"], [])
        assert raw == b"Id,Name\n"

    def test_lf_not_crlf(self) -> None:
        raw = _render_partition(["A"], [["x"]])
        assert b"\r" not in raw
