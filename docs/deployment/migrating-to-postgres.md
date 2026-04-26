---
title: Migrating from SQLite to Postgres
slug: migrating-to-postgres
nav_order: 40
tags: [deployment, database, migration, postgres, sqlite]
summary: >-
  One-shot CLI cutover from a SQLite-backed install to PostgreSQL for operators who need to scale up.
---

## What this covers / who should read this

Self-hosted operators who started on the default SQLite database and need to move to PostgreSQL — for example, because they are adding more users, running concurrent loads, or moving to the `aws_hosted` distribution profile.

This is a **one-way, offline cutover**. You stop the backend, run three CLI commands, flip one environment variable, and restart. Postgres → SQLite is not supported.

---

## Prerequisites

Before you begin:

- PostgreSQL is running and reachable from the machine where you will run the migration script. See the [Docker deployment guide](docker.md) for the Postgres overlay.
- `alembic upgrade head` has been run against the Postgres database (the compose overlay does this automatically on container start).
- The `ENCRYPTION_KEY` environment variable is set to the same value used by the backend. If you auto-generated it, find it in `/data/db/encryption.key` inside the container or on the host bind-mount.
- Python 3.12+ with the backend dependencies installed (or run the script inside the backend container).

---

## Step-by-step cutover

### 1. Back up the SQLite database

```bash
cp data/db/bulk_loader.db data/db/bulk_loader.db.bak
```

Do this while the backend is still running, so the WAL file is checkpointed into the main DB file. **Do not skip this step.** The migration script refuses to overwrite non-empty Postgres tables without `--i-have-a-backup`.

### 2. Stop the backend

```bash
docker compose stop backend
```

Postgres can stay up. Stopping the backend ensures no new writes land in the SQLite source during migration.

### 3. Validate (pre-flight)

```bash
export ENCRYPTION_KEY=$(cat data/db/encryption.key)

python scripts/migrate_sqlite_to_postgres.py validate \
  --source data/db/bulk_loader.db \
  --target postgresql+asyncpg://user:password@localhost:5432/bulk_loader
```

The `validate` command is read-only. It checks:

| # | Check | What it catches |
|---|-------|-----------------|
| 1 | Source file exists and is readable | Wrong path, permissions |
| 2 | Target Postgres reachable | Wrong host/port/credentials |
| 3 | Alembic versions match | One side hasn't run migrations to HEAD |
| 4 | `ENCRYPTION_KEY` decrypts sample rows | Wrong key — migration would produce unreadable ciphertexts |
| 5 | Target tables are empty | Would overwrite existing data |
| 6 | All ORM columns present on target | Schema drift between alembic versions |
| 7 | No NULL violations in source | Rows that Postgres would reject due to NOT NULL constraints |
| 8 | Backend not responding | Backend is still live and writing to source |

Exit code 0 means all checks passed. Resolve any reported blockers before continuing.

**Alembic version mismatch** — run `alembic upgrade head` against whichever side is behind:

```bash
# Inside the container pointed at the target Postgres:
docker compose run --rm backend alembic upgrade head
```

### 4. Migrate

```bash
python scripts/migrate_sqlite_to_postgres.py migrate \
  --source data/db/bulk_loader.db \
  --target postgresql+asyncpg://user:password@localhost:5432/bulk_loader
```

The `migrate` command:

1. Re-runs all validate checks. Aborts on any blocker.
2. Connects to source SQLite in read-only mode.
3. Walks tables in FK-safe topological order (parents before children).
4. Streams rows in batches of 1,000 (configurable with `--batch-size`) and bulk-inserts into Postgres.
5. All inserts run in **a single Postgres transaction**. Any failure rolls back the entire migration — partial state is never left behind.
6. Prints a per-table row-count summary at the end.

If migration fails, the Postgres database is left empty. Fix the reported error and re-run.

**Non-empty target** — if you are re-running after a partial attempt and the target already has data from a previous run, pass:

```bash
python scripts/migrate_sqlite_to_postgres.py migrate \
  --source data/db/bulk_loader.db \
  --target postgresql+asyncpg://... \
  --force --i-have-a-backup
```

### 5. Verify (post-flight)

```bash
python scripts/migrate_sqlite_to_postgres.py verify \
  --source data/db/bulk_loader.db \
  --target postgresql+asyncpg://user:password@localhost:5432/bulk_loader
```

The `verify` command is read-only on both databases. It checks:

- Row counts match for every table.
- A random sample of 5 rows per table is fetched from both sides and compared column-by-column.
- Encrypted columns (Salesforce private keys, S3 credentials, TOTP secrets) can be decrypted with `ENCRYPTION_KEY` on the Postgres side.

Exit code 0 means the migration is clean. Proceed to step 6.

### 6. Update `DATABASE_URL` and restart

In your `.env` file, change:

```diff
-DATABASE_URL=sqlite+aiosqlite:////data/db/bulk_loader.db
+DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/bulk_loader
```

Then restart the backend:

```bash
docker compose up -d backend
```

### 7. Smoke test

Log in, open a load plan, and trigger a small test run to confirm the migrated data is readable and the new Postgres connection works end-to-end.

---

## Encrypted columns

Private keys, S3 credentials, TOTP secrets, and any `app_settings` row with `is_encrypted=true` are Fernet ciphertexts. As long as `ENCRYPTION_KEY` is the same before and after the migration, the ciphertexts copy verbatim — no re-encryption needed. The `validate` and `verify` commands confirm this with a sample decrypt.

---

## Troubleshooting

| Error | Fix |
|-------|-----|
| `ENCRYPTION_KEY not set` | `export ENCRYPTION_KEY=$(cat data/db/encryption.key)` |
| `Alembic version mismatch` | Run `alembic upgrade head` against the lagging side |
| `Backend appears to be running` | `docker compose stop backend` |
| `NULL violations in NOT NULL column` | The source DB has rows from before a migration added a NOT NULL constraint. Identify and fix or delete them before migrating. |
| `Migration failed: relation "..." does not exist` | Run `alembic upgrade head` against the target Postgres |
| `Migration failed: value too long for type character varying(N)` | The source has a value that exceeds a Postgres column length limit. SQLite ignores length constraints. Truncate or correct the offending rows. |

---

## Related

- [Docker deployment guide](docker.md) — full self-hosted setup including the Postgres overlay
- [AWS deployment guide](aws.md) — AWS profile, which requires Postgres
- [Rotating the encryption key](../usage/connections.md) — if you need to change `ENCRYPTION_KEY` after migration
