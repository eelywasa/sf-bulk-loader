---
title: Authoring load plans
slug: load-plans
nav_order: 40
tags: [plans, steps, configuration]
required_permission: plans.manage
summary: >-
  Compose a multi-step load — ordering, operations, partition size, error
  threshold, abort behaviour.
---

# Authoring load plans

## What this covers / who should read this

How to compose a **Load Plan** — the template that defines what to load, in
what order, and how strict the error policy is. Read this after you have a
working Salesforce connection. Requires `plans.manage`.

---

## Anatomy of a plan

| Field | Purpose |
|---|---|
| **Name** | Free-text label shown in the UI. |
| **Connection** | The Salesforce connection used for every step. |
| **Output connection** | Optional S3 output — see [Output sinks](output-sinks.md). |
| **Max parallel jobs** | Maximum concurrent Bulk API jobs per run (default 5). Controls a semaphore around partition execution. |
| **Error threshold %** | Per-step failure percentage that counts as a failing step (default 10). |
| **Abort on step failure** | If a step exceeds the error threshold, abort the whole run. Default on. |
| **Steps** | An ordered list of what to load. |

A Plan is reusable — executing it creates a **Load Run**. See
[Running a load](running-loads.md).

---

## Steps

Add steps in **execution order** — parents before children. Each step
declares:

| Field | Description |
|---|---|
| **Sequence** | Ordering within the plan (managed by the drag handle). |
| **Step name** | Optional human-readable identifier (e.g. `stale_accounts`). Must be unique within the plan when set. Used to reference this step as an upstream input source. |
| **Object name** | Salesforce API name (`Account`, `Contact`, `Custom_Object__c`). For query steps this is a free-text label only. |
| **Operation** | One of `insert`, `update`, `upsert`, `delete`, `query`, `queryAll`. See table below. |
| **External ID field** | Required for `upsert`. The field Salesforce uses to decide insert-vs-update. |
| **CSV file pattern** | DML steps only — glob over the input location. See [CSV format → Glob patterns](csv-format.md#glob-patterns). Not used when **Input source** is set to "From upstream step". |
| **SOQL** | Query steps only — the statement to execute. |
| **Partition size** | Per-step override of the default partition size. |
| **Assignment rule** | Optional Salesforce assignment rule ID (Leads / Cases). |
| **Input source** | Three-way: local input, previous-run output (S3 or local-output), or **From upstream step in this run** — feeds a named query step's artefact directly into this DML step. See [Chaining steps](chaining-steps.md). |

### Operations

| Operation | What it does |
|---|---|
| `insert` | Creates new records. |
| `update` | Updates existing records — requires `Id` column. |
| `upsert` | Inserts or updates based on the external ID field. |
| `delete` | Soft-deletes records by `Id`. |
| `query` | Runs SOQL, writes results to a CSV artefact. See [Bulk queries](bulk-query.md). |
| `queryAll` | Same as query but includes soft-deleted and archived rows. |

---

## Error threshold & abort behaviour

At the end of every step the loader computes:

```
failure_pct = records_failed / records_processed * 100
```

If `failure_pct > error_threshold_pct`:

- If **Abort on step failure** is on → the run transitions to `aborted`, any
  in-flight jobs are best-effort aborted in Salesforce, and subsequent steps
  do **not** run.
- If **Abort on step failure** is off → the step is tallied as
  `completed_with_errors` and the run continues to the next step.

The threshold is evaluated **per step** — later steps don't retroactively
re-open earlier decisions.

---

## Previewing before you run

Click **Preview** on the plan page. For each DML step this:

- Resolves the glob against the current input location.
- Counts records.
- Shows the first few rows so you can spot header / encoding mistakes.

For query steps, use **Validate SOQL** instead — it calls Salesforce's
`explain` endpoint to check syntax and return the query plan. See
[Bulk queries → Validating SOQL](bulk-query.md#validating-soql).

---

## Editing an existing plan

Plans are editable as long as no run is currently `pending` or `running` against
them. Safe edits include adding/removing steps, re-ordering, and tuning
thresholds — none of these affect past runs.

---

## Related

- [CSV format](csv-format.md)
- [Running a load](running-loads.md) (next step)
- [Bulk queries](bulk-query.md)
- [Chaining steps](chaining-steps.md) — feed a query step's output into a DML step in the same run
- [Output sinks](output-sinks.md)
- [Notifications](notifications.md) — subscribe to run-completion events
