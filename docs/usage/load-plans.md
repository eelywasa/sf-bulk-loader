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
| **Object name** | Salesforce API name (`Account`, `Contact`, `Custom_Object__c`). Required — a step cannot be saved without one, and surrounding whitespace is trimmed. For query steps this is a free-text label only, but still required. |
| **Operation** | One of `insert`, `update`, `upsert`, `delete`, `query`, `queryAll`. See table below. |
| **External ID field** | Required for `upsert`. The field Salesforce uses to decide insert-vs-update. |
| **CSV file pattern** | DML steps only — glob over the input location. See [CSV format → Glob patterns](csv-format.md#glob-patterns). Not used when **Input source** is set to "From upstream step". |
| **SOQL** | Query steps only — the statement to execute. |
| **Partition size** | Per-step override of the default partition size. |
| **File encoding** | How the step's input files are decoded. Defaults to **UTF-8**; set to **Windows-1252** or **ISO-8859-1** only if you know the source encoding. Files are *not* auto-detected. See [Encoding](#file-encoding) below. |
| **Assignment rule** | Optional Salesforce assignment rule ID (Leads / Cases). |
| **Input source** | Three-way: **Input files**, **Previous-run output** (prior run results), or **From upstream step in this run** — feeds a named query step's artefact directly into this DML step. See [Chaining steps](chaining-steps.md). |

### Steps saved without an object

Plans created before the object name was enforced may contain a step with no
object set. Such a step is flagged **No object set** in the step list, and
opening it shows a validation message explaining the blank field. It cannot
run — it fails when the loader tries to create the Salesforce job — so set the
object and save.

Until it is corrected, that plan also cannot be duplicated: duplication is
refused with a message naming the offending step, rather than copying the
problem into a second plan. Nothing is deleted or guessed on your behalf —
only you know which object the step was meant to load. The backend logs the
affected step IDs once at startup.

### File encoding

Input files are read as **UTF-8** unless the step says otherwise. There is no
auto-detection.

Set **File encoding** when a source system exports Windows-1252 or ISO-8859-1.
If a file is not UTF-8 and no encoding is set, the run fails with a message
naming the offending byte and its position in the file — it does not load the
data incorrectly.

Three things are worth knowing:

- **A wrong setting does not fail.** It silently substitutes different
  characters and the load reports success. Only set this when you know the
  source encoding; leaving it on UTF-8 and reading the error is safer than
  guessing.
- **A file that mixes encodings cannot be loaded** under any setting. The
  error says so explicitly, and the file must be repaired at source.
- **ISO-8859-1 never fails.** It accepts every possible byte, so a step set to
  it can never report a decode problem — including when the file is actually
  something else.

Browsing a file in the **Files** pane is more forgiving: undecodable
characters are shown as replacement characters rather than blocking the
preview, because previewing never sends data to Salesforce. A file that
previews with odd characters will still fail to load until its encoding is
correct.

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
