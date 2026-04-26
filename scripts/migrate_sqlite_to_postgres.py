#!/usr/bin/env python3
"""SQLite → Postgres one-shot migration CLI (SFBL-272).

Three subcommands:

  validate  Read-only pre-flight. Exits 0 on green, non-zero on any blocker.
  migrate   Copies all rows; rolls back the entire target on any error.
  verify    Post-flight; reads both databases and compares row counts and
            sampled rows.

ENCRYPTION_KEY must be set in the environment to the same value the backend
uses. Both source and target must be at the same alembic revision before
migrate is run.

Usage:
    export ENCRYPTION_KEY=$(cat /data/db/encryption.key)

    python scripts/migrate_sqlite_to_postgres.py validate \\
        --source /data/db/bulk_loader.db \\
        --target postgresql+asyncpg://user:pass@localhost:5432/bulk_loader

    python scripts/migrate_sqlite_to_postgres.py migrate \\
        --source /data/db/bulk_loader.db \\
        --target postgresql+asyncpg://user:pass@localhost:5432/bulk_loader

    python scripts/migrate_sqlite_to_postgres.py verify \\
        --source /data/db/bulk_loader.db \\
        --target postgresql+asyncpg://user:pass@localhost:5432/bulk_loader
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from datetime import datetime as _dt, timezone as _tz
from pathlib import Path
from typing import Any

# ── Bootstrap: make 'app' importable without running uvicorn ──────────────────
_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

# Suppress .env loading so the developer's docker-compose env doesn't leak in.
os.environ["SFBL_DISABLE_ENV_FILE"] = "1"

# Settings() requires ENCRYPTION_KEY, JWT_SECRET_KEY, and ADMIN_EMAIL to
# either be set or have writable paths to auto-generate keys. Provide
# placeholders so the import succeeds; the validate command independently
# checks that ENCRYPTION_KEY is the real production value.
from cryptography.fernet import Fernet, InvalidToken  # noqa: E402  (before app imports)

_ENCRYPTION_KEY: str = os.environ.get("ENCRYPTION_KEY", "")
_KEY_FROM_ENV: bool = bool(_ENCRYPTION_KEY)
if not _KEY_FROM_ENV:
    # Temporary key so Settings() can construct; validate will refuse to proceed.
    os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()

for _placeholder_key, _placeholder_val in [
    ("JWT_SECRET_KEY", "migration-placeholder-jwt"),
    ("ADMIN_EMAIL", "migration@placeholder.local"),
]:
    os.environ.setdefault(_placeholder_key, _placeholder_val)

# Register all ORM models so Base.metadata.sorted_tables is complete.
import app.models  # noqa: F401, E402
from app.database import Base  # noqa: E402

from sqlalchemy import inspect, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncConnection,
    AsyncEngine,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402

# ── Encrypted-column registry ─────────────────────────────────────────────────
# Columns that hold Fernet ciphertexts. Nullable columns may be None — skip
# those in decrypt checks. app_settings is handled separately via is_encrypted.
ENCRYPTED_COLS: dict[str, list[str]] = {
    "connection": ["private_key", "access_token"],
    "input_connection": ["access_key_id", "secret_access_key", "session_token"],
    "user_totp": ["secret_encrypted"],
}

BATCH_SIZE = 1000
VERIFY_SAMPLE_SIZE = 5
BACKEND_HEALTH_URL = "http://localhost:8000/api/health/live"

# ── ANSI output helpers ───────────────────────────────────────────────────────
_IS_TTY = sys.stdout.isatty()
_G = "\033[32m"
_Y = "\033[33m"
_R = "\033[31m"
_B = "\033[1m"
_X = "\033[0m"


def _c(code: str, s: str) -> str:
    return f"{code}{s}{_X}" if _IS_TTY else s


def ok(msg: str) -> None:
    print(f"  {_c(_G, '✓')} {msg}")


def warn(msg: str) -> None:
    print(f"  {_c(_Y, '⚠')} {msg}")


def fail(msg: str) -> None:
    print(f"  {_c(_R, '✗')} {msg}")


def hdr(msg: str) -> None:
    print(f"\n{_c(_B, msg)}")


def info(msg: str) -> None:
    print(f"    {msg}")


# ── URL helpers ───────────────────────────────────────────────────────────────

def _resolve_source(source: str) -> tuple[str, str]:
    """Return (abs_file_path, aiosqlite_ro_url) for the source argument."""
    if source.startswith("sqlite"):
        # Strip driver prefix to get the raw path
        raw = source.split("///", 1)[-1]
        abs_path = str(Path(raw).resolve())
    else:
        abs_path = str(Path(source).resolve())
    ro_url = f"sqlite+aiosqlite:///file:{abs_path}?mode=ro&uri=true"
    return abs_path, ro_url


def _src_engine(ro_url: str) -> AsyncEngine:
    return create_async_engine(ro_url, poolclass=NullPool)


def _tgt_engine(target_url: str) -> AsyncEngine:
    return create_async_engine(target_url, poolclass=NullPool)


# ── Fernet decrypt helper ─────────────────────────────────────────────────────

def _fernet() -> Fernet:
    key = _ENCRYPTION_KEY if _KEY_FROM_ENV else os.environ.get("ENCRYPTION_KEY", "")
    return Fernet(key.encode() if isinstance(key, str) else key)


def _try_decrypt(fernet: Fernet, ciphertext: str | None, label: str) -> str | None:
    """Return None on success, an error string on failure. Skips None values."""
    if ciphertext is None:
        return None
    try:
        fernet.decrypt(ciphertext.encode())
        return None
    except InvalidToken:
        return f"{label}: decryption failed — wrong key or corrupt ciphertext"


# ── Shared helpers ────────────────────────────────────────────────────────────

async def _alembic_version(conn: AsyncConnection) -> str | None:
    try:
        row = await conn.execute(text("SELECT version_num FROM alembic_version"))
        return row.scalar_one_or_none()
    except Exception:
        return None


async def _table_count(conn: AsyncConnection, table_name: str) -> int:
    # Double-quote to avoid collision with reserved words (e.g. "user" in Postgres).
    result = await conn.execute(text(f'SELECT COUNT(*) FROM "{table_name}"'))
    return result.scalar_one()


def _sorted_model_tables():
    """Return model tables in FK-safe insertion order (parents before children)."""
    all_names = {t.name for t in Base.metadata.sorted_tables}
    return [t for t in Base.metadata.sorted_tables if t.name in all_names]


def _utc_aware_row(table, row: dict) -> dict:
    """Mark naive datetimes in TIMESTAMPTZ columns as UTC.

    asyncpg uses the OS timezone when encoding naive Python datetimes for
    TIMESTAMPTZ parameters.  SQLite stores datetimes as naive strings that
    represent UTC (since the backend always runs in UTC).  This helper stamps
    them explicitly so asyncpg stores them at the correct UTC instant.
    """
    from sqlalchemy.types import DateTime as _DateTime

    result = {}
    for col in table.columns:
        val = row.get(col.name)
        if (
            isinstance(col.type, _DateTime)
            and col.type.timezone
            and isinstance(val, _dt)
            and val.tzinfo is None
        ):
            val = val.replace(tzinfo=_tz.utc)
        result[col.name] = val
    return result


# ── validate ──────────────────────────────────────────────────────────────────

async def _run_validate(
    source: str,
    target_url: str,
    force: bool,
    backup_confirmed: bool,
    *,
    _for_migrate: bool = False,
) -> int:
    """Return number of blocking failures (0 = green)."""
    failures = 0
    abs_path, ro_url = _resolve_source(source)

    # 1. Source file ──────────────────────────────────────────────────────────
    hdr("1. Source SQLite")
    src_file = Path(abs_path)
    if not src_file.exists():
        fail(f"Not found: {abs_path}")
        return 1
    if not os.access(abs_path, os.R_OK):
        fail(f"Not readable: {abs_path}")
        return 1
    size_mb = src_file.stat().st_size / 1_048_576
    ok(f"{abs_path} ({size_mb:.1f} MB)")

    # 2. Target Postgres reachable ────────────────────────────────────────────
    hdr("2. Target Postgres")
    tgt_eng = _tgt_engine(target_url)
    try:
        async with tgt_eng.connect() as conn:
            await conn.execute(text("SELECT 1"))
        ok("Reachable")
    except Exception as exc:
        fail(f"Cannot connect: {exc}")
        await tgt_eng.dispose()
        return 1

    # 3. Alembic version ──────────────────────────────────────────────────────
    hdr("3. Alembic version")
    src_eng = _src_engine(ro_url)
    try:
        async with src_eng.connect() as src_conn, tgt_eng.connect() as tgt_conn:
            src_ver = await _alembic_version(src_conn)
            tgt_ver = await _alembic_version(tgt_conn)
    except Exception as exc:
        fail(f"Could not read alembic_version: {exc}")
        await src_eng.dispose()
        await tgt_eng.dispose()
        return 1

    if src_ver is None:
        fail("Source has no alembic_version — has alembic ever run against it?")
        failures += 1
    elif tgt_ver is None:
        fail("Target has no alembic_version — run `alembic upgrade head` first")
        failures += 1
    elif src_ver != tgt_ver:
        fail(f"Version mismatch: source={src_ver} target={tgt_ver}")
        info("Run `alembic upgrade head` against the lagging side first")
        failures += 1
    else:
        ok(f"Both at {src_ver}")

    # 4. ENCRYPTION_KEY ───────────────────────────────────────────────────────
    hdr("4. ENCRYPTION_KEY")
    if not _KEY_FROM_ENV:
        fail("ENCRYPTION_KEY not set — export it before running this script")
        failures += 1
        fernet = None
    else:
        fernet = _fernet()
        decrypt_errors: list[str] = []
        async with src_eng.connect() as src_conn:
            for table_name, cols in ENCRYPTED_COLS.items():
                try:
                    row = (
                        await src_conn.execute(
                            text(f'SELECT {", ".join(cols)} FROM "{table_name}" LIMIT 1')
                        )
                    ).mappings().first()
                except Exception:
                    continue  # table may be empty or not yet exist
                if row is None:
                    continue
                for col in cols:
                    err_msg = _try_decrypt(fernet, row.get(col), f"{table_name}.{col}")
                    if err_msg:
                        decrypt_errors.append(err_msg)

            # app_settings encrypted rows
            try:
                rows = (
                    await src_conn.execute(
                        text(
                            "SELECT value FROM app_settings "
                            "WHERE is_encrypted = 1 LIMIT 5"
                        )
                    )
                ).all()
                for r in rows:
                    err_msg = _try_decrypt(fernet, r[0], "app_settings.value")
                    if err_msg:
                        decrypt_errors.append(err_msg)
            except Exception:
                pass

        if decrypt_errors:
            for e in decrypt_errors:
                fail(e)
            failures += 1
        else:
            ok("Sample decrypt succeeded")

    # 5. Target tables empty ──────────────────────────────────────────────────
    hdr("5. Target tables empty")
    non_empty: list[str] = []
    async with tgt_eng.connect() as tgt_conn:
        for table in _sorted_model_tables():
            try:
                cnt = await _table_count(tgt_conn, table.name)
                if cnt > 0:
                    non_empty.append(f"{table.name} ({cnt:,} rows)")
            except Exception:
                pass  # table doesn't exist — schema check will catch it

    if non_empty:
        if force and backup_confirmed:
            warn("Non-empty tables (proceeding due to --force --i-have-a-backup):")
            for t in non_empty:
                info(t)
        else:
            fail("Target has existing data — migration would overwrite it:")
            for t in non_empty:
                info(t)
            if not force:
                info("Re-run with --force --i-have-a-backup to override")
            elif not backup_confirmed:
                info("--force requires --i-have-a-backup to proceed")
            failures += 1
    else:
        ok("All tables empty")

    # 6. Schema introspection diff ────────────────────────────────────────────
    hdr("6. Schema vs ORM")
    schema_issues: list[str] = []

    def _do_schema_inspect(sync_conn) -> dict[str, set[str] | None]:
        """Return {table_name: set(col_names)} for each ORM table on the target."""
        inspector = inspect(sync_conn)
        live = set(inspector.get_table_names())
        result: dict[str, set[str] | None] = {}
        for tbl in _sorted_model_tables():
            if tbl.name not in live:
                result[tbl.name] = None
            else:
                result[tbl.name] = {c["name"] for c in inspector.get_columns(tbl.name)}
        return result

    async with tgt_eng.connect() as tgt_conn:
        col_map = await tgt_conn.run_sync(_do_schema_inspect)

    for table in _sorted_model_tables():
        cols = col_map.get(table.name)
        if cols is None:
            schema_issues.append(f"Table missing on target: {table.name}")
            continue
        for col in table.columns:
            if col.name not in cols:
                schema_issues.append(
                    f"{table.name}.{col.name}: column missing on target"
                )

    if schema_issues:
        for issue in schema_issues:
            fail(issue)
        failures += 1
    else:
        ok("All ORM columns present on target")

    # 7. Type-drift scan on source (NULL in NOT NULL columns) ─────────────────
    hdr("7. Type-drift scan (source)")
    drift_issues: list[str] = []
    async with src_eng.connect() as src_conn:
        for table in _sorted_model_tables():
            for col in table.columns:
                if col.nullable or col.primary_key:
                    continue
                try:
                    result = await src_conn.execute(
                        text(
                            f'SELECT COUNT(*) FROM "{table.name}" '
                            f"WHERE {col.name} IS NULL"
                        )
                    )
                    null_count = result.scalar_one()
                    if null_count > 0:
                        drift_issues.append(
                            f"{table.name}.{col.name}: "
                            f"{null_count:,} NULL(s) in NOT NULL column"
                        )
                except Exception:
                    pass

    if drift_issues:
        for issue in drift_issues:
            warn(issue)
        info("These rows will cause migrate to fail — fix source data first")
        if not _for_migrate:
            failures += 1
    else:
        ok("No NULL violations found")

    # 8. Backend liveness check ───────────────────────────────────────────────
    hdr("8. Backend liveness")
    try:
        import httpx  # optional dep; skip gracefully if missing

        resp = httpx.get(BACKEND_HEALTH_URL, timeout=2.0)
        if resp.status_code < 500:
            fail(
                f"Backend appears to be running ({resp.status_code}). "
                "Stop it before migrating to avoid writes to the source SQLite."
            )
            failures += 1
        else:
            ok("Backend not responding (expected)")
    except ImportError:
        warn("httpx not installed — skipping backend liveness check")
    except Exception:
        ok("Backend not responding (expected)")

    await src_eng.dispose()
    await tgt_eng.dispose()

    hdr("Summary")
    if failures == 0:
        ok("All checks passed — safe to run migrate")
    else:
        fail(f"{failures} blocker(s) found — resolve before migrating")

    return failures


# ── migrate ───────────────────────────────────────────────────────────────────

async def _run_migrate(
    source: str,
    target_url: str,
    force: bool,
    backup_confirmed: bool,
    batch_size: int,
) -> int:
    hdr("Pre-flight validate")
    failures = await _run_validate(
        source, target_url, force, backup_confirmed, _for_migrate=True
    )
    if failures > 0:
        fail("Aborting — fix validation errors first")
        return 1

    abs_path, ro_url = _resolve_source(source)
    src_eng = _src_engine(ro_url)
    tgt_eng = _tgt_engine(target_url)

    tables = _sorted_model_tables()
    src_counts: dict[str, int] = {}
    tgt_counts: dict[str, int] = {}

    hdr("Migration")
    print()

    try:
        async with src_eng.connect() as src_conn:
            for table in tables:
                cnt = await _table_count(src_conn, table.name)
                src_counts[table.name] = cnt

        async with tgt_eng.begin() as tgt_conn:
            for table in tables:
                total = src_counts.get(table.name, 0)
                if total == 0:
                    info(f"{table.name}: 0 rows (skipped)")
                    tgt_counts[table.name] = 0
                    continue

                inserted = 0
                offset = 0
                print(f"  Copying {table.name} ({total:,} rows) ...", end="", flush=True)

                async with src_eng.connect() as src_conn:
                    while True:
                        rows = (
                            await src_conn.execute(
                                select(table).offset(offset).limit(batch_size)
                            )
                        ).mappings().all()
                        if not rows:
                            break
                        await tgt_conn.execute(
                            table.insert(),
                            [_utc_aware_row(table, dict(r)) for r in rows],
                        )
                        inserted += len(rows)
                        offset += batch_size
                        print(".", end="", flush=True)

                print(f" {inserted:,} rows")
                tgt_counts[table.name] = inserted

    except Exception as exc:
        print()
        fail(f"Migration failed: {exc}")
        info("All target changes have been rolled back")
        await src_eng.dispose()
        await tgt_eng.dispose()
        return 1

    # Stamp alembic_version on target to match source
    async with src_eng.connect() as src_conn:
        src_ver = await _alembic_version(src_conn)
    if src_ver:
        async with tgt_eng.begin() as tgt_conn:
            existing = await _alembic_version(tgt_conn)
            if existing is None:
                await tgt_conn.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                    {"v": src_ver},
                )
            else:
                await tgt_conn.execute(
                    text("UPDATE alembic_version SET version_num = :v"),
                    {"v": src_ver},
                )
        ok(f"alembic_version stamped: {src_ver}")

    await src_eng.dispose()
    await tgt_eng.dispose()

    hdr("Row-count summary")
    print(f"  {'Table':<40} {'Source':>10} {'Target':>10}")
    print(f"  {'-'*40} {'-'*10} {'-'*10}")
    all_match = True
    for table in tables:
        s = src_counts.get(table.name, 0)
        t = tgt_counts.get(table.name, 0)
        match = "✓" if s == t else "✗"
        if s != t:
            all_match = False
        print(f"  {table.name:<40} {s:>10,} {t:>10,} {match}")

    print()
    if all_match:
        ok("Migration complete — all row counts match")
        info("Next: run verify, then update DATABASE_URL and restart the backend")
    else:
        warn("Some row counts differ — run verify for details")

    return 0


# ── verify ────────────────────────────────────────────────────────────────────

async def _run_verify(source: str, target_url: str) -> int:
    abs_path, ro_url = _resolve_source(source)
    src_eng = _src_engine(ro_url)
    tgt_eng = _tgt_engine(target_url)
    tables = _sorted_model_tables()
    failures = 0

    hdr("1. Row counts")
    async with src_eng.connect() as src_conn, tgt_eng.connect() as tgt_conn:
        for table in tables:
            s = await _table_count(src_conn, table.name)
            t = await _table_count(tgt_conn, table.name)
            if s == t:
                ok(f"{table.name}: {s:,}")
            else:
                fail(f"{table.name}: source={s:,} target={t:,}")
                failures += 1

    hdr("2. Sampled-row comparison")
    async with src_eng.connect() as src_conn, tgt_eng.connect() as tgt_conn:
        for table in tables:
            pk_cols = [c for c in table.columns if c.primary_key]
            if not pk_cols:
                continue
            pk_col = pk_cols[0]

            # Fetch a random sample of PKs from the source
            all_pks = (
                await src_conn.execute(select(pk_col))
            ).scalars().all()
            if not all_pks:
                continue
            sample = random.sample(all_pks, min(VERIFY_SAMPLE_SIZE, len(all_pks)))

            mismatches = 0
            for pk_val in sample:
                src_row = (
                    await src_conn.execute(
                        select(table).where(pk_col == pk_val)
                    )
                ).mappings().first()
                tgt_row = (
                    await tgt_conn.execute(
                        select(table).where(pk_col == pk_val)
                    )
                ).mappings().first()

                if tgt_row is None:
                    mismatches += 1
                    continue

                for col in table.columns:
                    sv = src_row.get(col.name) if src_row else None
                    tv = tgt_row.get(col.name)
                    if sv != tv:
                        # Normalise datetimes: strip timezone info before comparing.
                        # Postgres TIMESTAMPTZ columns return tz-aware datetimes;
                        # SQLite returns tz-naive.  Both represent the same instant
                        # after correct migration, so we compare the naive wall-clock.
                        from datetime import datetime as _dt
                        def _norm(v: Any) -> Any:
                            if isinstance(v, _dt) and v.tzinfo is not None:
                                return v.replace(tzinfo=None)
                            return v
                        if _norm(sv) == _norm(tv):
                            continue
                        sv_s = str(sv) if sv is not None else None
                        tv_s = str(tv) if tv is not None else None
                        if sv_s != tv_s:
                            mismatches += 1
                            info(
                                f"{table.name}.{col.name} pk={pk_val}: "
                                f"src={sv_s!r} tgt={tv_s!r}"
                            )

            if mismatches:
                fail(f"{table.name}: {mismatches} mismatch(es) in sampled rows")
                failures += 1
            else:
                ok(f"{table.name}: {len(sample)} rows sampled OK")

    hdr("3. Decrypt-roundtrip (target)")
    if not _KEY_FROM_ENV:
        warn("ENCRYPTION_KEY not set — skipping decrypt check")
    else:
        fernet = _fernet()
        decrypt_errors: list[str] = []
        async with tgt_eng.connect() as tgt_conn:
            for table_name, cols in ENCRYPTED_COLS.items():
                try:
                    row = (
                        await tgt_conn.execute(
                            text(
                                f"SELECT {', '.join(cols)} FROM {table_name} LIMIT 1"
                            )
                        )
                    ).mappings().first()
                except Exception:
                    continue
                if row is None:
                    continue
                for col in cols:
                    err_msg = _try_decrypt(fernet, row.get(col), f"{table_name}.{col}")
                    if err_msg:
                        decrypt_errors.append(err_msg)

            try:
                rows = (
                    await tgt_conn.execute(
                        text(
                            "SELECT value FROM app_settings "
                            "WHERE is_encrypted = true LIMIT 5"
                        )
                    )
                ).all()
                for r in rows:
                    err_msg = _try_decrypt(fernet, r[0], "app_settings.value")
                    if err_msg:
                        decrypt_errors.append(err_msg)
            except Exception:
                pass

        if decrypt_errors:
            for e in decrypt_errors:
                fail(e)
            failures += 1
        else:
            ok("Encrypted column decrypt-roundtrip passed on target")

    await src_eng.dispose()
    await tgt_eng.dispose()

    hdr("Summary")
    if failures == 0:
        ok("Verify passed — migration looks clean")
        info("Update DATABASE_URL in .env and restart the backend")
    else:
        fail(f"{failures} issue(s) found")

    return failures


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SQLite → Postgres one-shot migration (SFBL-272)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--source",
        required=True,
        help="SQLite file path or sqlite+aiosqlite:// URL",
    )
    shared.add_argument(
        "--target",
        required=True,
        help="Postgres URL e.g. postgresql+asyncpg://user:pass@host:5432/db",
    )

    val = sub.add_parser("validate", parents=[shared], help="Read-only pre-flight checks")
    val.add_argument("--force", action="store_true", help="Continue past non-empty target")
    val.add_argument(
        "--i-have-a-backup",
        action="store_true",
        dest="backup_confirmed",
        help="Required alongside --force to override non-empty target check",
    )

    mig = sub.add_parser("migrate", parents=[shared], help="Copy data from SQLite to Postgres")
    mig.add_argument("--force", action="store_true", help="Continue past non-empty target")
    mig.add_argument(
        "--i-have-a-backup",
        action="store_true",
        dest="backup_confirmed",
        help="Required alongside --force to override non-empty target check",
    )
    mig.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        dest="batch_size",
        help=f"Rows per batch (default {BATCH_SIZE})",
    )

    sub.add_parser("verify", parents=[shared], help="Post-migration row-count and sample check")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "validate":
        rc = asyncio.run(
            _run_validate(
                args.source,
                args.target,
                getattr(args, "force", False),
                getattr(args, "backup_confirmed", False),
            )
        )
    elif args.command == "migrate":
        rc = asyncio.run(
            _run_migrate(
                args.source,
                args.target,
                args.force,
                args.backup_confirmed,
                args.batch_size,
            )
        )
    elif args.command == "verify":
        rc = asyncio.run(_run_verify(args.source, args.target))
    else:
        parser.print_help()
        rc = 1

    sys.exit(min(rc, 1))


if __name__ == "__main__":
    main()
