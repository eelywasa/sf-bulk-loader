---
title: Running a load
slug: running-loads
nav_order: 50
tags: [runs, monitoring, abort, retry]
required_permission: runs.execute
summary: >-
  Trigger a run, monitor it live, abort, and retry a failed step.
---

# Running a load

## What this covers / who should read this

How to execute a Load Plan and supervise the run end-to-end. Requires
`runs.execute` to start and retry; `runs.abort` to abort; `runs.view` to
monitor (these are the permissions held by `operator` and `admin` by default).

---

## Triggering a run

From the plan page click **Run**. This creates a `LoadRun` in `pending`
status and kicks off an asyncio background task in the backend that becomes
`running` within a second or two.

A run proceeds through the plan's steps in `sequence` order:

1. Resolve the step's CSV glob (or S3 prefix) to a list of files.
2. Partition each file.
3. Create one **Job** per partition.
4. Execute all partitions in parallel, bounded by the plan's
   `max_parallel_jobs` semaphore.
5. When all jobs in the step are terminal, evaluate the error threshold —
   possibly aborting the run.

---

## Monitoring a run

Navigate to the run on **Runs → Run detail**. The page receives live updates
over WebSocket (`/ws/runs/{run_id}`) — no manual refresh needed.

Panels:

- **Summary** — status, elapsed time, totals (`records_processed`,
  `records_failed`).
- **Step list** — each step with progress by job, status, and a drill-in
  link.
- **Job detail** — per-partition view with raw Salesforce payload, result
  file links, and logs.

The page stops polling as soon as the run reaches a terminal state
(`completed`, `completed_with_errors`, `failed`, or `aborted`).

---

## Run statuses

| Status | Meaning |
|---|---|
| `pending` | Created; background task not yet started. |
| `running` | At least one step is executing. |
| `completed` | All steps completed with no failures beyond the error threshold. |
| `completed_with_errors` | Finished, but some step exceeded its threshold and the plan allows continuing. |
| `failed` | Run hit an unrecoverable error outside step-level error accounting (e.g. auth failure). |
| `aborted` | Run was stopped — user-triggered abort, or a step exceeded the threshold with abort-on-step-failure on. |

---

## Aborting a run

Click **Abort** on the run detail page. Confirm the modal.

The run status flips to `aborted` and the backend best-effort aborts any
Bulk API jobs still in flight on Salesforce. Jobs already in a terminal state
are left as-is. Result CSVs already written are preserved.

Abort is **cooperative** — the background task checks the flag between
partitions. Expect 1-2 polling intervals of latency before jobs stop.

Requires `runs.abort`.

---

## Retrying a failed step

If a step ended `completed_with_errors` or aborted because of the error
threshold, you can retry just the failed rows:

1. Open the run detail page.
2. Find the failing step and click **Retry step**.
3. The loader creates new partitions consisting only of the failed rows from
   the previous attempt and executes them as if they were a fresh step.

The retry is tracked as its own step execution within the existing run. The
original error rows remain in the original partitions' error CSVs — the retry
only writes new files for its attempt.

Requires `runs.execute`. See
[`backend/app/api/load_runs.py`](../../backend/app/api/load_runs.py).

---

## Where the results go

Result CSVs and the per-run `logs.zip` are written to the **output sink**
configured on the plan. See [The Files pane](files-pane.md) for the layout
and [Output sinks](output-sinks.md) for local vs S3.

---

## Related

- [Authoring load plans](load-plans.md)
- [The Files pane](files-pane.md) — inspecting results
- [Notifications](notifications.md) — get pinged on terminal status
- Architecture: [Run execution](../architecture/run-execution.md)
