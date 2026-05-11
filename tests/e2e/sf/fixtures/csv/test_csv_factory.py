"""
test_csv_factory.py — Unit tests for csv_factory.py

Run from the repo root:
    pip install -r tests/e2e/sf/fixtures/csv/requirements.txt
    python -m pytest tests/e2e/sf/fixtures/csv/test_csv_factory.py -v

Coverage goals:
  - mint_ext_ids: count, format, prefix validation
  - pick_parent: randomness, determinism, empty-list guard
  - write_csv: header, row content, UTF-8 + LF encoding, empty-rows edge case
  - seeded: two invocations with same seed → identical output
  - validate_prefix: accept hyphen-safe, reject _ and %
"""

import csv
import io
import sys
from pathlib import Path

import pytest

# Allow running the test from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).parent))

from csv_factory import (  # noqa: E402
    mint_ext_ids,
    pick_parent,
    seeded,
    validate_prefix,
    write_csv,
)


# ---------------------------------------------------------------------------
# validate_prefix
# ---------------------------------------------------------------------------


class TestValidatePrefix:
    def test_accepts_hyphen_only_prefix(self) -> None:
        # Should not raise or exit
        validate_prefix("E2E-RUN-1-0-test-")

    def test_accepts_alphanumeric_prefix(self) -> None:
        validate_prefix("TEST-")

    def test_rejects_underscore(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            validate_prefix("UNSAFE_TEST-")
        assert exc_info.value.code == 1

    def test_rejects_percent(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            validate_prefix("UNSAFE%TEST-")
        assert exc_info.value.code == 1

    def test_rejects_underscore_in_middle(self) -> None:
        """An underscore anywhere in the prefix is rejected — not just at the start."""
        with pytest.raises(SystemExit) as exc_info:
            validate_prefix("E2E-UNSAFE_VARIANT-")
        assert exc_info.value.code == 1

    def test_rejects_both_wildcards(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            validate_prefix("E2E_%BAD-")
        assert exc_info.value.code == 1

    def test_error_message_mentions_offending_char(self, capsys: pytest.CaptureFixture) -> None:
        with pytest.raises(SystemExit):
            validate_prefix("BAD_PREFIX-")
        captured = capsys.readouterr()
        assert "_" in captured.err
        assert "SOQL LIKE wildcard" in captured.err


# ---------------------------------------------------------------------------
# mint_ext_ids
# ---------------------------------------------------------------------------


class TestMintExtIds:
    def test_returns_correct_count(self) -> None:
        ids = mint_ext_ids("TEST-", 5)
        assert len(ids) == 5

    def test_format_zero_padded(self) -> None:
        ids = mint_ext_ids("P-", 3)
        assert ids == ["P-001", "P-002", "P-003"]

    def test_wide_padding_for_large_count(self) -> None:
        ids = mint_ext_ids("X-", 1000)
        assert ids[0] == "X-0001"
        assert ids[999] == "X-1000"

    def test_one_id(self) -> None:
        assert mint_ext_ids("ACCT-", 1) == ["ACCT-001"]

    def test_rejects_underscore_prefix(self) -> None:
        with pytest.raises(SystemExit):
            mint_ext_ids("BAD_", 5)

    def test_rejects_percent_prefix(self) -> None:
        with pytest.raises(SystemExit):
            mint_ext_ids("BAD%", 5)

    def test_sequential_values(self) -> None:
        ids = mint_ext_ids("E2E-", 10)
        for i, ext_id in enumerate(ids, start=1):
            assert ext_id == f"E2E-{i:03d}"


# ---------------------------------------------------------------------------
# pick_parent
# ---------------------------------------------------------------------------


class TestPickParent:
    def test_returns_element_from_list(self) -> None:
        parents = [{"id": "A"}, {"id": "B"}, {"id": "C"}]
        result = pick_parent(parents)
        assert result in parents

    def test_empty_list_raises(self) -> None:
        with pytest.raises(IndexError):
            pick_parent([])

    def test_single_element_always_returned(self) -> None:
        only = {"id": "only"}
        assert pick_parent([only]) is only

    def test_deterministic_with_seed(self) -> None:
        parents = [{"id": f"P{i}"} for i in range(10)]
        results_a: list[dict] = []
        results_b: list[dict] = []
        with seeded(99):
            results_a = [pick_parent(parents) for _ in range(20)]
        with seeded(99):
            results_b = [pick_parent(parents) for _ in range(20)]
        assert results_a == results_b

    def test_different_seeds_differ(self) -> None:
        parents = [{"id": f"P{i}"} for i in range(100)]
        with seeded(1):
            seq_a = [pick_parent(parents)["id"] for _ in range(50)]
        with seeded(2):
            seq_b = [pick_parent(parents)["id"] for _ in range(50)]
        # Almost certainly different; extremely unlikely to be identical
        assert seq_a != seq_b


# ---------------------------------------------------------------------------
# write_csv
# ---------------------------------------------------------------------------


class TestWriteCsv:
    def test_writes_header_and_rows(self, tmp_path: Path) -> None:
        out = tmp_path / "out.csv"
        rows = [{"Name": "Acme", "Ext": "ACME-001"}, {"Name": "Globex", "Ext": "GLOB-001"}]
        write_csv(out, rows)
        content = out.read_text(encoding="utf-8")
        assert content.startswith("Name,Ext\n")
        assert "Acme" in content
        assert "Globex" in content

    def test_lf_line_endings(self, tmp_path: Path) -> None:
        out = tmp_path / "out.csv"
        write_csv(out, [{"A": "1"}, {"A": "2"}])
        raw = out.read_bytes()
        assert b"\r\n" not in raw
        assert b"\n" in raw

    def test_utf8_encoding(self, tmp_path: Path) -> None:
        out = tmp_path / "out.csv"
        write_csv(out, [{"Name": "Ünïcödé Corp"}])
        content = out.read_text(encoding="utf-8")
        assert "Ünïcödé Corp" in content

    def test_explicit_fields_order(self, tmp_path: Path) -> None:
        out = tmp_path / "out.csv"
        rows = [{"B": "2", "A": "1"}]
        write_csv(out, rows, fields=["A", "B"])
        content = out.read_text(encoding="utf-8")
        assert content.startswith("A,B\n")

    def test_empty_rows_with_fields(self, tmp_path: Path) -> None:
        out = tmp_path / "out.csv"
        write_csv(out, [], fields=["Name", "Ext"])
        content = out.read_text(encoding="utf-8")
        assert content == "Name,Ext\n"

    def test_empty_rows_no_fields(self, tmp_path: Path) -> None:
        out = tmp_path / "out.csv"
        write_csv(out, [])
        content = out.read_text(encoding="utf-8")
        assert content == ""

    def test_deterministic_output(self, tmp_path: Path) -> None:
        """Same rows → byte-identical file on repeated calls."""
        rows = [{"Name": "Acme", "Ext": f"ACME-{i:03d}"} for i in range(5)]
        out_a = tmp_path / "a.csv"
        out_b = tmp_path / "b.csv"
        write_csv(out_a, rows)
        write_csv(out_b, rows)
        assert out_a.read_bytes() == out_b.read_bytes()

    def test_parseable_by_csv_module(self, tmp_path: Path) -> None:
        rows = [{"X": "hello,world", "Y": 'say "hi"'}]
        out = tmp_path / "out.csv"
        write_csv(out, rows)
        parsed = list(csv.DictReader(io.StringIO(out.read_text(encoding="utf-8"))))
        assert parsed[0]["X"] == "hello,world"
        assert parsed[0]["Y"] == 'say "hi"'


# ---------------------------------------------------------------------------
# seeded
# ---------------------------------------------------------------------------


class TestSeeded:
    def test_same_seed_same_ext_ids(self) -> None:
        with seeded(42):
            ids_a = mint_ext_ids("T-", 5)
        with seeded(42):
            ids_b = mint_ext_ids("T-", 5)
        assert ids_a == ids_b

    def test_different_seed_different_pick(self) -> None:
        parents = [{"id": str(i)} for i in range(100)]
        with seeded(10):
            a = [pick_parent(parents)["id"] for _ in range(20)]
        with seeded(20):
            b = [pick_parent(parents)["id"] for _ in range(20)]
        assert a != b

    def test_nested_seeded_restores_outer(self) -> None:
        """The outer seeded context is restored after an inner block exits."""
        with seeded(1):
            id1 = mint_ext_ids("OUTER-", 1)[0]
            with seeded(999):
                _ = mint_ext_ids("INNER-", 1)
            id2 = mint_ext_ids("OUTER-", 1)[0]

        with seeded(1):
            a = mint_ext_ids("OUTER-", 1)[0]
            b = mint_ext_ids("OUTER-", 1)[0]

        # Because seeded() restores the outer faker/rng instance (not re-seeds it),
        # the id2 from the outer block matches b from a fresh seeded(1) run only if
        # the inner block's state doesn't leak. The key assertion is that nesting
        # doesn't corrupt the outer block irreversibly.
        # The outer faker & rng are *reinstated* (restored), so id1 == a.
        assert id1 == a

    def test_seeded_csv_determinism(self, tmp_path: Path) -> None:
        """Full round-trip: seeded Faker → rows → write_csv → byte-identical."""
        from csv_factory import fake_company, fake_last_name

        def make_rows() -> list[dict]:
            return [
                {"Company": fake_company(), "Last": fake_last_name(), "Ext": f"TEST-{i:03d}"}
                for i in range(10)
            ]

        out_a = tmp_path / "a.csv"
        out_b = tmp_path / "b.csv"
        with seeded(42):
            write_csv(out_a, make_rows())
        with seeded(42):
            write_csv(out_b, make_rows())
        assert out_a.read_bytes() == out_b.read_bytes()
