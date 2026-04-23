# Storage architecture

## What this covers / who should read this

How the Bulk Loader discovers input CSVs, writes result files, encrypts secrets at rest, and exposes files via the API. Read this if you are wiring up a new input source, debugging an output sink, or reasoning about encryption and key handling.

---

## Input discovery

Input CSVs live under a configured directory (default `data/input/`, `INPUT_DIR` overrides). `LoadStep.csv_file_pattern` is a glob (e.g. `accounts_*.csv`, `subdir/**/*.csv`) that is resolved at step execution time.

Resolution is handled in [`backend/app/services/csv_processor.py`](../../backend/app/services/csv_processor.py) and the input-storage layer under [`backend/app/services/`](../../backend/app/services):

1. Validate the pattern — `..` path-traversal segments are rejected.
2. Expand the glob within `INPUT_DIR`.
3. Re-validate every resolved path stays inside `INPUT_DIR` (defence-in-depth).
4. Return a sorted list of absolute paths.

Invalid patterns or escapes raise `InputStorageError`, which the API surfaces as a 400.

### Partitioning

Each discovered CSV is streamed through `partition_csv()` into fixed-size chunks:

- **Default partition size** — `default_partition_size` from settings (DB-backed since SFBL-156).
- **Per-step override** — `LoadStep.partition_size`.
- **Memory profile** — streaming via the stdlib `csv` module; at most one partition's rows in memory.
- **Encoding** — input encodings (latin-1, cp1252, UTF-8 ± BOM) are detected and re-emitted as UTF-8 with LF line endings (required by Salesforce Bulk API 2.0).
- **Headers** — the original CSV header row is preserved on every partition.

Example: a 50 000-row CSV with partition size 10 000 yields 5 partitions and therefore 5 `JobRecord` rows, processed concurrently subject to `LoadPlan.max_parallel_jobs`.

### S3 input connections

For hosted profiles, a `LoadStep` may set `input_connection_id` to an `InputConnection` of type `s3`. The same glob + partitioning pipeline applies, but files are streamed directly from the bucket. Credentials live on the `InputConnection` row (encrypted).

See [`docs/s3-connection-setup.md`](../s3-connection-setup.md) for operator configuration.

---

## Output sinks

### Local (default)

Results land under `OUTPUT_DIR` (default `data/output/`):

```
data/output/
└── {run_id}/
    ├── {step_id}/
    │   ├── {partition_index}_success.csv
    │   ├── {partition_index}_error.csv
    │   └── {partition_index}_unprocessed.csv
    └── logs.zip          # bundled run logs
```

Paths are stored on `JobRecord.success_file_path` / `error_file_path` / `unprocessed_file_path` **relative** to `OUTPUT_DIR`, so environments are portable.

### S3

If a `LoadPlan` has `output_connection_id` pointing to an S3 `InputConnection`, result CSVs are uploaded instead of (or in addition to) local. The layout above is preserved under the configured prefix.

See [`backend/app/services/output_storage.py`](../../backend/app/services/output_storage.py) — `get_output_storage()` is the factory.

### Retention

No automated cleanup. Files remain on disk or in S3 until manually removed. If automated retention is required it belongs as a separate Jira story, not in the orchestrator.

---

## Encryption at rest

### Salesforce private keys

`Connection.private_key` stores the RSA key used for Salesforce JWT Bearer auth. It is Fernet-encrypted before insert.

- **Key source** — the `ENCRYPTION_KEY` env var, or a file at `encryption_key_file` (auto-generated with a warning log on first boot if the env var is unset).
- **Decryption** — happens inside [`backend/app/services/salesforce_auth.py`](../../backend/app/services/salesforce_auth.py) when minting a token; plaintext never leaves the service call.
- **Key rotation** — **not automated**. Rotating the Fernet key requires decrypting every `Connection.private_key` under the old key and re-encrypting under the new one. Plan this as a break-glass operation rather than a routine.

### Other secrets

- `JWT_SECRET_KEY` — HS256 signing key for user sessions. Auto-generated to `jwt_secret_key_file` if unset.
- PostgreSQL password — passed via `DATABASE_URL` only; never written to disk.
- SMTP credentials — stored encrypted in the `app_setting` table. See [`docs/email.md`](../email.md).
- Admin bootstrap password — consumed once at first boot, then discarded (the hash is persisted on the `User` row).

---

## Files API & permission gating

Under `/api/files/` and `/api/jobs/{id}/…-csv(/preview)` and `/api/runs/{id}/logs.zip`:

| Permission | What it unlocks |
|---|---|
| `files.view` | List files, see metadata (size, mtime) |
| `files.view_contents` | Download CSVs, preview rows, download `logs.zip` |

A viewer profile holds `files.view` but **not** `files.view_contents`, so it can browse existence but not read PII (enforced by SFBL-206 — see [`backend/tests/test_permission_matrix.py`](../../backend/tests/test_permission_matrix.py)).

See [`docs/specs/rbac-permission-matrix.md`](../specs/rbac-permission-matrix.md) for the authoritative matrix.
