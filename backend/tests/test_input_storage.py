"""Tests for the LocalInputStorage service and detect_encoding utility."""

import csv
import os
import pathlib
import tempfile

import pytest

from app.services.input_storage import (
    InputStorageError,
    LocalInputStorage,
    detect_encoding,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _write_csv(path: str, rows: int = 3) -> None:
    """Write a minimal CSV with a header and *rows* data rows."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Name", "Value"])
        for i in range(rows):
            writer.writerow([f"item_{i}", str(i)])


# ── detect_encoding ───────────────────────────────────────────────────────────


def test_detect_encoding_utf8(tmp_path):
    f = tmp_path / "utf8.csv"
    f.write_bytes("Name,Value\nAlpha,1\n".encode("utf-8"))
    assert detect_encoding(f) == "utf-8-sig"


def test_detect_encoding_utf8_bom(tmp_path):
    f = tmp_path / "bom.csv"
    f.write_bytes("Name,Value\nAlpha,1\n".encode("utf-8-sig"))
    assert detect_encoding(f) == "utf-8-sig"


def test_detect_encoding_cp1252(tmp_path):
    f = tmp_path / "cp1252.csv"
    # Byte 0x80 is the Euro sign in cp1252; it is invalid in utf-8.
    f.write_bytes(b"Name\nCaf\x80\n")
    assert detect_encoding(f) == "cp1252"


def test_detect_encoding_latin1(tmp_path):
    f = tmp_path / "latin1.csv"
    # Byte 0x81 is undefined in cp1252 (raises UnicodeDecodeError) but valid in
    # latin-1 (which maps all 256 byte values).  Forces the latin-1 fallback.
    f.write_bytes(b"Name\n\x81\n")
    assert detect_encoding(f) == "latin-1"


# ── Path safety ───────────────────────────────────────────────────────────────


def test_safe_path_rejects_double_dot(tmp_path):
    storage = LocalInputStorage(str(tmp_path))
    assert storage._safe_path("../outside") is None


def test_safe_path_rejects_embedded_double_dot(tmp_path):
    storage = LocalInputStorage(str(tmp_path))
    assert storage._safe_path("sub/../../outside") is None


def test_safe_path_accepts_valid_relative(tmp_path):
    (tmp_path / "sub").mkdir()
    storage = LocalInputStorage(str(tmp_path))
    result = storage._safe_path("sub")
    assert result is not None
    assert result.is_dir()


# ── list_entries ──────────────────────────────────────────────────────────────


def test_list_entries_empty_dir(tmp_path):
    storage = LocalInputStorage(str(tmp_path))
    assert storage.list_entries() == []


def test_list_entries_returns_csvs_only(tmp_path):
    _write_csv(str(tmp_path / "accounts.csv"))
    (tmp_path / "readme.txt").write_text("ignore me")
    storage = LocalInputStorage(str(tmp_path))
    entries = storage.list_entries()
    names = [e.name for e in entries]
    assert "accounts.csv" in names
    assert "readme.txt" not in names


def test_list_entries_directories_first(tmp_path):
    (tmp_path / "zfolder").mkdir()
    _write_csv(str(tmp_path / "accounts.csv"))
    storage = LocalInputStorage(str(tmp_path))
    entries = storage.list_entries()
    assert entries[0].kind == "directory"
    assert entries[1].kind == "file"


def test_list_entries_dotfiles_excluded(tmp_path):
    (tmp_path / ".hidden").mkdir()
    _write_csv(str(tmp_path / "visible.csv"))
    storage = LocalInputStorage(str(tmp_path))
    names = [e.name for e in storage.list_entries()]
    assert ".hidden" not in names
    assert "visible.csv" in names


def test_list_entries_has_size_and_row_count(tmp_path):
    _write_csv(str(tmp_path / "data.csv"), rows=5)
    storage = LocalInputStorage(str(tmp_path))
    entry = storage.list_entries()[0]
    assert entry.size_bytes is not None and entry.size_bytes > 0
    assert entry.row_count == 5


def test_list_entries_subdirectory(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    _write_csv(str(sub / "deep.csv"), rows=2)
    storage = LocalInputStorage(str(tmp_path))
    entries = storage.list_entries("sub")
    assert len(entries) == 1
    assert entries[0].name == "deep.csv"
    assert entries[0].path == "sub/deep.csv"
    assert entries[0].row_count == 2


def test_list_entries_traversal_raises(tmp_path):
    storage = LocalInputStorage(str(tmp_path))
    with pytest.raises(InputStorageError):
        storage.list_entries("../outside")


def test_list_entries_nonexistent_path_raises(tmp_path):
    storage = LocalInputStorage(str(tmp_path))
    with pytest.raises(InputStorageError):
        storage.list_entries("nonexistent")


def test_list_entries_missing_base_returns_empty(tmp_path):
    storage = LocalInputStorage(str(tmp_path / "does_not_exist"))
    assert storage.list_entries() == []


# ── preview_file ──────────────────────────────────────────────────────────────


def test_preview_file_returns_header_and_rows(tmp_path):
    _write_csv(str(tmp_path / "data.csv"), rows=20)
    storage = LocalInputStorage(str(tmp_path))
    preview = storage.preview_file("data.csv", rows=5)
    assert preview.filename == "data.csv"
    assert preview.header == ["Name", "Value"]
    assert len(preview.rows) == 5
    assert preview.row_count == 5


def test_preview_file_respects_row_limit(tmp_path):
    _write_csv(str(tmp_path / "data.csv"), rows=3)
    storage = LocalInputStorage(str(tmp_path))
    preview = storage.preview_file("data.csv", rows=100)
    assert len(preview.rows) == 3  # only 3 data rows exist


def test_preview_file_traversal_raises(tmp_path):
    storage = LocalInputStorage(str(tmp_path))
    with pytest.raises(InputStorageError):
        storage.preview_file("../secret.csv", rows=10)


def test_preview_file_not_found_raises(tmp_path):
    storage = LocalInputStorage(str(tmp_path))
    with pytest.raises(FileNotFoundError):
        storage.preview_file("nonexistent.csv", rows=10)


def test_preview_file_cp1252_encoding(tmp_path):
    """Files encoded as cp1252 should preview without errors."""
    csv_path = tmp_path / "cp1252.csv"
    # Write header + 1 row with a cp1252-specific byte
    csv_path.write_bytes(b"Name\nCaf\x80\n")
    storage = LocalInputStorage(str(tmp_path))
    preview = storage.preview_file("cp1252.csv", rows=10)
    assert preview.header == ["Name"]
    assert len(preview.rows) == 1


def test_preview_file_latin1_encoding(tmp_path):
    """Files with unmapped cp1252 bytes should fall back to latin-1."""
    csv_path = tmp_path / "latin1.csv"
    csv_path.write_bytes(b"Name\n\x81\n")
    storage = LocalInputStorage(str(tmp_path))
    preview = storage.preview_file("latin1.csv", rows=10)
    assert preview.header == ["Name"]
    assert len(preview.rows) == 1


# ── discover_files ────────────────────────────────────────────────────────────


def test_discover_files_matches_pattern(tmp_path):
    for name in ("accounts_1.csv", "accounts_2.csv", "contacts.csv"):
        _write_csv(str(tmp_path / name))
    storage = LocalInputStorage(str(tmp_path))
    found = storage.discover_files("accounts_*.csv")
    assert len(found) == 2
    assert all(p.name.startswith("accounts_") for p in found)


def test_discover_files_sorted(tmp_path):
    for name in ("c.csv", "a.csv", "b.csv"):
        _write_csv(str(tmp_path / name))
    storage = LocalInputStorage(str(tmp_path))
    found = storage.discover_files("*.csv")
    assert [p.name for p in found] == ["a.csv", "b.csv", "c.csv"]


def test_discover_files_regular_files_only(tmp_path):
    _write_csv(str(tmp_path / "real.csv"))
    (tmp_path / "subdir.csv").mkdir()  # directory masquerading as CSV name
    storage = LocalInputStorage(str(tmp_path))
    found = storage.discover_files("*.csv")
    assert all(p.is_file() for p in found)


def test_discover_files_traversal_raises(tmp_path):
    storage = LocalInputStorage(str(tmp_path))
    with pytest.raises(InputStorageError):
        storage.discover_files("../outside/*.csv")


def test_discover_files_no_match_returns_empty(tmp_path):
    storage = LocalInputStorage(str(tmp_path))
    assert storage.discover_files("nonexistent_*.csv") == []
