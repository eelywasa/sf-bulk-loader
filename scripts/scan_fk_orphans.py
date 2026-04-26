#!/usr/bin/env python3
"""Scan a SQLite database for orphaned foreign-key references.

The connect-event listener in ``app.database`` did not apply
``PRAGMA foreign_keys=ON`` on SQLite from project inception until commit
``c554767`` on 2026-04-25 (SFBL-166): the listener gated by
``isinstance(dbapi_connection, sqlite3.Connection)``, but ``aiosqlite``
wraps the underlying connection in its own adapter so the body never ran.
Every ``ON DELETE CASCADE`` / ``ON DELETE SET NULL`` declared in the schema
was a runtime no-op for the lifetime of any SQLite database created or
written to before the fix.

This script walks every FK in the live schema (read out of
``Base.metadata`` so it stays in sync with the models) and counts rows
whose non-NULL FK column points at a missing parent. It is read-only.

Usage::

    python scripts/scan_fk_orphans.py --db /path/to/sf_bulk_loader.db
    python scripts/scan_fk_orphans.py                         # uses DATABASE_URL

Exit codes:
    0 — no orphans found in any FK
    1 — orphans found (counts printed per FK)
    2 — invalid arguments / DB unreachable
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse


def _resolve_default_db_path() -> str | None:
    url = os.environ.get("DATABASE_URL")
    if not url or not url.startswith("sqlite"):
        return None
    parsed = urlparse(url)
    path = parsed.path
    if path.startswith("/./"):
        path = path[1:]
    return path or None


def _iter_fks() -> Iterator[tuple[str, str, str, str]]:
    """Yield (child_table, child_col, parent_table, parent_col) for every FK.

    Reads from the live SQLAlchemy metadata so the script stays in sync with
    the models — no manual list to maintain.
    """
    repo_root = Path(__file__).resolve().parent.parent
    backend = repo_root / "backend"
    sys.path.insert(0, str(backend))
    os.environ.setdefault("ENCRYPTION_KEY", "x" * 44)
    os.environ.setdefault("SFBL_DISABLE_ENV_FILE", "1")
    os.environ.setdefault("JWT_SECRET_KEY", "scan-fk-orphans-script")

    from app.database import Base  # noqa: WPS433
    import app.models  # noqa: F401, WPS433  # populate Base.metadata

    for table in Base.metadata.sorted_tables:
        for fk in table.foreign_keys:
            yield (
                table.name,
                fk.parent.name,
                fk.column.table.name,
                fk.column.name,
            )


def scan(db_path: str) -> int:
    if not Path(db_path).exists():
        print(f"error: database file not found: {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path)
    try:
        total_orphans = 0
        any_orphan_fk = False
        print(f"Scanning {db_path} for FK orphans…\n")
        print(f"{'CHILD':<40} {'PARENT':<40} {'ORPHANS':>8}")
        print("-" * 90)
        for child_t, child_c, parent_t, parent_c in _iter_fks():
            sql = (
                f"SELECT COUNT(*) FROM {child_t} c "
                f"LEFT JOIN {parent_t} p ON c.{child_c} = p.{parent_c} "
                f"WHERE c.{child_c} IS NOT NULL AND p.{parent_c} IS NULL"
            )
            try:
                count = conn.execute(sql).fetchone()[0]
            except sqlite3.OperationalError as exc:
                print(
                    f"{child_t}.{child_c:<20} -> {parent_t}.{parent_c:<20} "
                    f"SKIP ({exc})"
                )
                continue
            label_l = f"{child_t}.{child_c}"
            label_r = f"-> {parent_t}.{parent_c}"
            print(f"{label_l:<40} {label_r:<40} {count:>8}")
            total_orphans += count
            if count > 0:
                any_orphan_fk = True
        print("-" * 90)
        print(f"Total orphan rows across all FKs: {total_orphans}")
        return 1 if any_orphan_fk else 0
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--db",
        default=None,
        help="Path to SQLite database file. Defaults to the SQLite path in DATABASE_URL.",
    )
    args = parser.parse_args()
    db_path = args.db or _resolve_default_db_path()
    if not db_path:
        print(
            "error: no --db given and DATABASE_URL is not a sqlite URL",
            file=sys.stderr,
        )
        return 2
    return scan(db_path)


if __name__ == "__main__":
    sys.exit(main())
