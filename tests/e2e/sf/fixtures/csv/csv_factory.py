"""
csv_factory.py — Faker-backed shared CSV helpers for Salesforce-shaped test data.

Salesforce-shaped, app-blind: this module knows about Salesforce record patterns
(ExternalId formatting, parent-lookup fields) but nothing about the bulk-loader
application under test. It lives under sf/fixtures/ per the D13 import-direction
rule (app/ may import sf/, not the reverse).

D3 prefix discipline (hard-locked):
  All consumer scripts accept a --prefix CLI arg. The prefix must be hyphen-safe:
  underscores (_) and percent-signs (%) are SOQL LIKE wildcards that produce
  false-match cleanup queries. This module validates prefixes at the point of use
  and raises SystemExit on violation so no CSV is written with a rogue prefix.

Usage:
    from csv_factory import mint_ext_ids, pick_parent, write_csv, seeded, validate_prefix

    with seeded(42):
        ids = mint_ext_ids("TEST-", 5)     # ["TEST-001", "TEST-002", ...]
        parent = pick_parent(rows)
        write_csv(Path("out.csv"), rows)
"""

import contextlib
import csv
import io
import random
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Optional

from faker import Faker

# ---------------------------------------------------------------------------
# Module-level Faker instance.  Re-seeded by the seeded() context manager.
# ---------------------------------------------------------------------------

_faker = Faker()
_rng = random.Random()

# ---------------------------------------------------------------------------
# Prefix validation (D3 — hard-locked)
# ---------------------------------------------------------------------------

# SOQL LIKE wildcards: underscore = single char, percent = any sequence.
# Reject both in prefix values so cleanup queries remain safe.
_SOQL_WILDCARD_RE = re.compile(r"[_%]")


def validate_prefix(prefix: str) -> None:
    """Raise SystemExit if *prefix* contains SOQL LIKE wildcard characters.

    This is a *fail-fast* guard: if the validator trips, no CSV file is
    written.  The exit code is 1 and the message names the offending
    character(s) so the caller can fix the prefix immediately.

    Args:
        prefix: The prefix string to validate.

    Raises:
        SystemExit: If the prefix contains ``_`` or ``%``.
    """
    bad = _SOQL_WILDCARD_RE.findall(prefix)
    if bad:
        chars = ", ".join(repr(c) for c in sorted(set(bad)))
        print(
            f"ERROR: prefix {prefix!r} contains SOQL LIKE wildcard character(s): "
            f"{chars}. Use hyphens only — underscores and percent-signs produce "
            "false-match cleanup queries (see D3 in e2e-testing-spec.md).",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Seeded context manager
# ---------------------------------------------------------------------------


@contextmanager
def seeded(seed: int) -> Generator[None, None, None]:
    """Context manager: seed Faker and the internal RNG for deterministic output.

    Both the Faker instance and the module-level ``random.Random`` are re-seeded
    on entry and restored to a fresh un-seeded state on exit (by re-instantiating
    them).  This means callers that wrap two ``seeded()`` blocks with the same
    seed get byte-identical CSV output each time.

    Args:
        seed: Integer seed value.

    Yields:
        Nothing — the side-effect is the seeded state.
    """
    global _faker, _rng
    old_faker = _faker
    old_rng = _rng
    Faker.seed(seed)
    _faker = Faker()
    _rng = random.Random(seed)
    try:
        yield
    finally:
        _faker = old_faker
        _rng = old_rng


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------


def mint_ext_ids(prefix: str, count: int) -> list[str]:
    """Generate *count* unique ExternalId strings using *prefix*.

    Format: ``{prefix}{n:03d}`` — zero-padded to at least 3 digits.
    Example: ``mint_ext_ids("TEST-", 3)`` → ``["TEST-001", "TEST-002", "TEST-003"]``.

    Validates the prefix via :func:`validate_prefix` before generating anything.

    Args:
        prefix: String prefix (must not contain SOQL wildcard chars ``_`` or ``%``).
        count: Number of IDs to generate.

    Returns:
        A list of *count* ExternalId strings.

    Raises:
        SystemExit: If the prefix fails SOQL wildcard validation.
    """
    validate_prefix(prefix)
    width = max(3, len(str(count)))
    return [f"{prefix}{i + 1:0{width}d}" for i in range(count)]


def pick_parent(parents: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a randomly-chosen row from *parents*.

    Useful for assigning a child record a random parent reference from a
    previously-generated list of parent rows.

    Uses the module-level ``_rng`` instance so that ``seeded()`` makes
    parent selection deterministic.

    Args:
        parents: Non-empty list of parent row dicts (typically the output
            of a prior :func:`mint_ext_ids` + row-builder pass).

    Returns:
        One element from *parents* chosen at random.

    Raises:
        IndexError: If *parents* is empty.
    """
    if not parents:
        raise IndexError("pick_parent: parents list must not be empty")
    return _rng.choice(parents)


def write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    fields: Optional[list[str]] = None,
) -> None:
    """Write *rows* to *path* as a UTF-8, LF-line-ending CSV.

    The column order follows *fields* when supplied; otherwise it is derived
    from the keys of the first row (insertion-ordered as of Python 3.7+).

    The output is byte-deterministic: given the same *rows* and *fields* the
    resulting file is bit-for-bit identical across runs, platforms, and Python
    versions (3.7+).

    Args:
        path: Destination file path.  Parent directory must exist.
        rows: List of row dicts.  All dicts must have the same keys.
        fields: Optional explicit column order.  Defaults to ``list(rows[0].keys())``.
    """
    if not rows:
        # Write a header-only CSV rather than an empty file so downstream
        # tools (e.g. the bulk loader) can detect the column shape.
        if fields is None:
            path.write_text("", encoding="utf-8", newline="")
            return
        buf = io.StringIO(newline="")
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(fields)
        path.write_text(buf.getvalue(), encoding="utf-8", newline="")
        return

    fieldnames = fields if fields is not None else list(rows[0].keys())
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(
        buf,
        fieldnames=fieldnames,
        lineterminator="\n",
        extrasaction="ignore",
    )
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8", newline="")


# ---------------------------------------------------------------------------
# Faker accessors (thin wrappers so callers don't import Faker directly)
# ---------------------------------------------------------------------------


def fake_company() -> str:
    """Return a random company name using the seeded Faker instance."""
    return _faker.company()


def fake_first_name() -> str:
    """Return a random first name using the seeded Faker instance."""
    return _faker.first_name()


def fake_last_name() -> str:
    """Return a random last name using the seeded Faker instance."""
    return _faker.last_name()


def fake_email(first: str, last: str, domain: Optional[str] = None) -> str:
    """Return a deterministic email from first/last + optional domain.

    Args:
        first: First name.
        last: Last name.
        domain: Optional domain name.  Defaults to ``example.com`` to avoid
            hitting real SMTP infrastructure during test runs.

    Returns:
        A lowercase email address.
    """
    d = domain or "example.com"
    return f"{first.lower()}.{last.lower()}@{d}"


def fake_title() -> str:
    """Return a random job title using the seeded Faker instance."""
    return _faker.job()


def fake_phone() -> str:
    """Return a random US-style phone number using the seeded Faker instance."""
    return _faker.phone_number()


def fake_amount(low: float = 1_000.0, high: float = 500_000.0) -> str:
    """Return a random decimal amount formatted as a string with 2dp.

    Args:
        low: Lower bound (inclusive).
        high: Upper bound (inclusive).

    Returns:
        Amount string, e.g. ``"42500.00"``.
    """
    value = _rng.uniform(low, high)
    return f"{value:.2f}"


def fake_close_date(year: int = 2025) -> str:
    """Return a random close date in YYYY-MM-DD format within *year*.

    Args:
        year: The calendar year to generate within.

    Returns:
        ISO date string.
    """
    month = _rng.randint(1, 12)
    day = _rng.randint(1, 28)  # safe across all months
    return f"{year}-{month:02d}-{day:02d}"


def fake_stage() -> str:
    """Return a random Salesforce Opportunity Stage value."""
    stages = [
        "Prospecting",
        "Qualification",
        "Needs Analysis",
        "Value Proposition",
        "Id. Decision Makers",
        "Perception Analysis",
        "Proposal/Price Quote",
        "Negotiation/Review",
        "Closed Won",
        "Closed Lost",
    ]
    return _rng.choice(stages)
