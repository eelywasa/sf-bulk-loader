---
title: Chaining steps in a plan
slug: chaining-steps
nav_order: 75
tags: [plans, query, dml]
required_permission: plans.manage
summary: >-
  Use a query step's output as the input to a later DML step in the same run.
---

# Chaining steps in a plan

## What this covers / who should read this

How to wire a **query step's output directly into a downstream DML step** within
the same Load Run, without manually specifying file paths. This is useful when
you want to query Salesforce for a set of record IDs and immediately act on them
(delete, update, etc.) in the same run.

Read [Bulk queries](bulk-query.md) first — step chaining requires at least one
query step in your plan. Requires `plans.manage`.

---

## How it works

When you configure a DML step to use **"From upstream step"** as its input
source:

1. At run time the orchestrator executes the upstream query step first (it has a
   lower sequence number).
2. The query step writes its results to a CSV artefact (local disk or S3,
   depending on your plan's output connection).
3. The orchestrator then resolves the artefact path from the upstream step's
   job record and feeds it directly to the downstream DML step as its input.
4. The DML step partitions and processes the CSV exactly as it would any other
   input source.

No file path wiring is needed. The path is resolved at the start of each run
from the run's own job records.

---

## Step naming

Before you can reference an upstream step you need to give it a **name**. The
name is optional but required for the dropdown in the step editor to show a
meaningful label.

- Navigate to your plan and open the step editor for the query step.
- Fill in **Step name** (e.g. `stale_accounts`). Names are trimmed; an empty
  name is stored as blank (no name).
- Names must be unique within a plan. The UI will warn you if you enter a
  duplicate.
- If you leave the name blank, the step's label in the UI will be
  `Step {sequence}: {operation} {object_name}`.

---

## Worked example: query stale accounts and delete them

### Step 1 — query step

| Field | Value |
|---|---|
| **Step name** | `stale_accounts` |
| **Object name** | `Account` |
| **Operation** | `query` |
| **SOQL** | `SELECT Id FROM Account WHERE LastModifiedDate < LAST_N_DAYS:90` |

This step fetches the `Id` of every Account that hasn't been modified in 90
days and writes the result to a single CSV artefact.

### Step 2 — DML step

| Field | Value |
|---|---|
| **Object name** | `Account` |
| **Operation** | `delete` |
| **Input source** | `From upstream step in this run` → `stale_accounts` |

The `csv_file_pattern` and `Input connection` fields are disabled when
**"From upstream step"** is selected.

### What happens at run time

1. The orchestrator runs Step 1 (`query`). Salesforce returns a list of `Id`
   values; the query step writes them to `{run_short}/steps/stale_accounts/results.csv`
   (or an S3 equivalent).
2. The orchestrator resolves that path from Step 1's job record and passes the
   file to Step 2 (`delete`) as its input.
3. Step 2 partitions the file according to the plan's partition size, submits
   Bulk API delete jobs, and polls for results.
4. Circuit breaker, error threshold, and abort-on-step-failure all apply to
   Step 2 normally.

---

## Column contract

The downstream DML step receives exactly what the upstream SOQL selects — no
column projection or renaming happens at reference time.

Make sure your SOQL includes the columns the downstream operation requires:

| Operation | Required column(s) |
|---|---|
| `delete` | `Id` |
| `update` | `Id` + every field you want to update |
| `upsert` | External ID field + every field you want to upsert |
| `insert` | Every field to insert (no `Id` needed) |

If the upstream SOQL omits a required column (e.g. `SELECT Name FROM Account`
for a `delete` step), the Bulk API job will be rejected when the CSV is
uploaded. The run will fail with a Salesforce field error — not a validation
error — so check your SOQL before running.

---

## Retry behaviour

When you **retry** a run, the orchestrator replays the downstream DML step from
its already-partitioned `JobRecord` entries. The upstream query step is **not**
re-executed — the resolver uses the artefact written during the original run.

This means:
- Retrying a failed `delete` step re-reads the same CSV the original query
  produced, not a fresh query result.
- If you want a fresh query, start a new run rather than retrying.

---

## Local disk vs S3 output sinks

Step chaining works with both output sink types:

| Plan output connection | How the artefact is stored | How the resolver reads it |
|---|---|---|
| None (local disk) | Relative path under `settings.output_dir` | `LocalInputStorage` — uses the stored relative path directly |
| S3 `InputConnection` | `s3://bucket/{full_key}` URI stored in `JobRecord.success_file_path` | `S3InputStorage` — parses the URI authoritatively; `root_prefix` is set to `""` so the full key is used as-is |

The resolver always reads the artefact from the URI stored in the job record,
not from the `InputConnection`'s current configuration. Changing the
connection's `root_prefix` after a run does not affect retry reads of that
run's artefacts.

---

## Validation errors you might see

| Error | Cause |
|---|---|
| `input_from_step_id cannot be combined with csv_file_pattern` | Both input source fields are set. Clear `csv_file_pattern` when using an upstream step. |
| `input_from_step_id cannot be combined with input_connection_id` | Both source fields set. Clear `input_connection_id`. |
| `Referenced step must be a query or queryAll step` | You pointed a DML step at another DML step. v1 supports query→DML only. |
| `Referenced step must have a lower sequence than this step` | The upstream step comes after the downstream step in the plan. Reorder or clear the reference. |
| `Reorder would invert an existing step reference` | Reordering would make a reference invalid (forward reference). Clear or repoint the reference, then reorder. |
| `Step name already used in this plan` | Two steps have the same name. Names must be unique within a plan. |

---

## Limitations (v1)

- **Query→DML only.** A DML step cannot reference another DML step's output
  (`success` or `error` CSV). This is tracked as a future epic.
- **No column projection.** The resolver passes the upstream CSV as-is. Field
  renaming or filtering at reference time is a future feature.
- **No SOQL templating.** You cannot parameterise the upstream SOQL with values
  from a previous step (e.g. `WHERE Id IN :upstream.Id`). This is a separate
  planned feature.

---

## Related

- [Bulk queries](bulk-query.md) — how query steps work, SOQL validation, output artefacts
- [Authoring load plans](load-plans.md) — plan anatomy, step ordering, error threshold
- [Output sinks](output-sinks.md) — local vs S3 output configuration
- [Running a load](running-loads.md) — trigger, monitor, abort, retry
