# Run execution architecture

## What this covers / who should read this

The full lifecycle of a load run — from the user clicking "Start" to terminal status — including partitioning, Salesforce Bulk API interaction, polling, retries, concurrency, abort semantics, and result persistence. Read this before touching the orchestrator or adding features that extend execution behaviour.

---

## Big picture

A `LoadPlan` is a template; a `LoadRun` is one execution of it. Execution is an asyncio background task supervised by FastAPI's lifespan. The orchestrator in [`backend/app/services/orchestrator.py`](../../backend/app/services/orchestrator.py) delegates to a small set of collaborators:

- **run coordinator** — sequences steps and manages run-level state transitions
- **step executor** — resolves input files, partitions them, dispatches jobs
- **partition executor** — drives one partition through the Salesforce Bulk API lifecycle
- **result persistence** — downloads and records success / error / unprocessed CSVs
- **run event publisher** — broadcasts state changes over WebSocket

High-level flow per run:

1. Iterate `LoadStep`s in ascending `sequence`.
2. For each step: discover CSVs → partition → create one `JobRecord` per partition → run all partitions concurrently (bounded by `LoadPlan.max_parallel_jobs`).
3. After each step: evaluate error threshold. If exceeded and `abort_on_step_failure=True`, abort the run and stop sequencing.
4. On completion, resolve terminal status (`completed` | `completed_with_errors` | `failed` | `aborted`).

---

## Step & job lifecycle

### LoadStep ([`load_step.py`](../../backend/app/models/load_step.py))

- `sequence` — execution order within a plan
- `object_name` — Salesforce object API name
- `operation` — `insert` | `update` | `upsert` | `delete` | `query` | `queryAll`
- `csv_file_pattern` — glob for DML operations; null for queries
- `soql` — for `query` / `queryAll`
- `external_id_field` — required when `operation='upsert'`
- `partition_size` — per-step override; null means "use the DB-backed default"
- `assignment_rule_id` — optional Salesforce assignment rule
- `input_connection_id` — `None` / `"local"` = local input tree, `"local-output"` = previous-run output, otherwise an `InputConnection` UUID (S3)

### JobRecord ([`job.py`](../../backend/app/models/job.py))

One row per partition per run. Key fields:

- `status` — `pending` → `uploading` → `upload_complete` → `in_progress` → `job_complete` | `failed` | `aborted`
- `sf_job_id` — assigned after creation on Salesforce
- `partition_index` — 0-based within the step
- `total_records`, `records_processed`, `records_failed` — from Salesforce state responses
- `success_file_path`, `error_file_path`, `unprocessed_file_path` — relative paths under `OUTPUT_DIR`
- `sf_api_response` — JSON of the last state object (for debugging)
- `started_at`, `completed_at`, `error_message`

---

## Polling strategy

Polling is in [`salesforce_bulk.py`](../../backend/app/services/salesforce_bulk.py).

- Initial interval: `SF_POLL_INTERVAL_INITIAL` (default 5 s)
- Max interval: `SF_POLL_INTERVAL_MAX` (default 30 s)
- Backoff: double on each tick, clamp at max (5 → 10 → 20 → 30 → 30 …)
- Absolute timeout: `sf_job_max_poll_seconds` (DB-backed since SFBL-111, default 3600 s). Set to `0` to disable.

On timeout a `BulkJobPollTimeout` (subclass of `BulkAPIError`) is raised, the job is marked `failed` with `error_message` set, and the run continues with remaining partitions. Whether the run as a whole aborts depends on the error threshold (§ Error threshold & abort).

---

## Concurrency & partitioning

### Semaphore

Each run creates `asyncio.Semaphore(plan.max_parallel_jobs)` (default 5). Partition tasks acquire it before talking to Salesforce; excess partitions queue.

### Session isolation (critical invariant)

Every partition task must own its own `AsyncSession`:

```python
async with db_session_factory() as db:
    job = await db.get(JobRecord, job_id)
    ...
```

Sharing a session across concurrent coroutines causes dirty reads, lost updates, and rollback surprises. If you extend the orchestrator, preserve this.

### Partitioning

`LoadStep.partition_size` takes precedence; otherwise the DB-backed `default_partition_size` setting applies (SFBL-156). A safety ceiling (`MAX_PARTITION_SIZE`) prevents configuration mistakes. Partitioning is done by [`partition_csv()`](../../backend/app/services/csv_processor.py) — streaming, header-preserving, UTF-8 LF output.

See [`docs/architecture/storage.md`](storage.md) for the partitioning details and input-discovery rules.

---

## HTTP retries

Bulk API calls route through `_request()` in `salesforce_bulk.py`:

- Retryable: 5xx server errors, 429 rate limits, `httpx` network errors
- Non-retryable: 4xx (other), 401 (auth), 404 (job gone)
- Attempts: `_MAX_RETRIES` = 3
- Backoff: 1 s → 2 s → 4 s exponential; `Retry-After` honoured on 429
- Exhaustion: raises `BulkAPIError` carrying the last status + body

---

## Salesforce auth

JWT Bearer OAuth 2.0 ([`salesforce_auth.py`](../../backend/app/services/salesforce_auth.py)):

1. Build an RS256-signed JWT with `iss=client_id`, `sub=username`, `aud=login_url`, `exp=now+180s` (Salesforce enforces a 3-minute cap).
2. POST the assertion to `/services/oauth2/token`; receive `access_token` + `instance_url`.
3. Salesforce tokens live ~2 h; the response omits `expires_in` so we manage expiry ourselves with a 300 s refresh buffer.
4. The token is cached on the `Connection` row, so it survives worker restarts.

A cached token is returned if it still has >300 s of life; otherwise a new JWT exchange runs. The private key is Fernet-decrypted on the fly — see [`docs/architecture/storage.md`](storage.md#encryption-at-rest).

---

## Error threshold & abort

Per step:

- `LoadPlan.error_threshold_pct` (default 10.0) — failure percentage tolerated.
- `LoadPlan.abort_on_step_failure` (default True) — whether exceeding the threshold aborts the whole run.

After all partitions in a step finish:

1. `total = Σ records_processed`, `failed = Σ records_failed`.
2. `pct = failed / total * 100`.
3. If `pct > threshold` and `abort_on_step_failure`:
   - Run status → `aborted`.
   - Best-effort abort of any still-running Salesforce jobs for this run.
   - Step sequencing halts; subsequent steps do not run.
4. Otherwise proceed to the next step, even if the threshold was exceeded with `abort_on_step_failure=False` (the step counts as `completed_with_errors` in tallying).

Threshold is evaluated **per step**; later steps don't retroactively re-open earlier decisions.

---

## Live updates

The run event publisher broadcasts on `/ws/runs/{run_id}`. The React `useLiveRun` hook consumes these and mirrors them into React Query caches so UI cards and tables rerender without manual refresh.

Terminal statuses — `completed`, `completed_with_errors`, `failed`, `aborted` — stop further broadcasts. The frontend hook detects terminal status and stops polling.

---

## Result files

After a Bulk API job completes, the partition executor downloads:

- **Success** — records Salesforce accepted
- **Error** — records Salesforce rejected (with per-row error messages)
- **Unprocessed** — records never processed (rare; only if the job was aborted mid-flight)

Paths are persisted to the `JobRecord` relative to `OUTPUT_DIR`. Download endpoints require both `runs.view` and `files.view_contents` (SFBL-206). See [`docs/architecture/storage.md`](storage.md#files-api--permission-gating).

---

## Retrying a failed step

Failed steps can be retried from the run detail UI — this creates new partitions for just the failed rows of that step and re-runs them. Permission: `runs.execute`. See [`backend/app/api/load_runs.py`](../../backend/app/api/load_runs.py) and the retry service module for the flow.
