# Named Step Outputs & Cross-Step References

**Status:** Live spec — implementation in progress (SFBL-166).
This file describes the locked design for the named-step-outputs feature.
It is **not** archived until all five stories (SFBL-261–265) are done and
the epic PR is merged.

---

## Background

SFBL-114 (Bulk Query) lets a query step write its results to an artefact at a
run-scoped output path. SFBL-164 defines that path layout
(`{plan_short}-{plan_slug}/{run_short}/...`). Because the full path only
exists once a run starts, a downstream DML step cannot reference it at
plan-edit time — the operator had to manually hard-code the prior run's short
id, which only worked across **separate** runs.

This epic closes the **same-run** gap: a DML step can declare that its input
should be resolved from the artefact produced by an upstream query step in the
**same** run, without wiring any file paths.

---

## Data model additions

### `LoadStep.name` (D1)

```sql
ALTER TABLE load_step ADD COLUMN name VARCHAR(255) NULL;

CREATE UNIQUE INDEX uq_load_step_plan_name
    ON load_step(load_plan_id, name) WHERE name IS NOT NULL;
```

- **Optional.** `NULL` by default; no data migration for existing rows.
- **Unique within plan** when set — enforced by the partial unique index above.
- When `NULL`, the UI computes a display label: `Step {sequence}: {operation} {object_name}`.
- New steps auto-fill a slug-style default (e.g. `query_account_ids`) that the
  user can edit or clear.

Schema-layer normalization (applied on both create and update):
- Leading/trailing whitespace is trimmed.
- Empty string after trim → stored as `NULL`.

This ensures the partial unique index does not reject plans where multiple
steps were saved with an empty name field.

### `LoadStep.input_from_step_id` (D2)

```sql
ALTER TABLE load_step ADD COLUMN input_from_step_id VARCHAR(36) NULL
    REFERENCES load_step(id) ON DELETE SET NULL;
```

- FK to `load_step.id` with `ON DELETE SET NULL` — if the upstream step is
  deleted the reference becomes `NULL` (the plan editor will surface a
  validation error on next save).
- The DB stores the **immutable UUID**; the UI shows the upstream step's `name`
  (or computed label).
- Using an ID FK rather than a name string means plan duplication remaps the
  reference cleanly and renames of the upstream step do not break the ref.

---

## Validation rules

### v1 scope: query→DML only (D3, locked)

`input_from_step_id` is only valid when **both** of the following hold:

| Side | Constraint |
|---|---|
| **Referencing step** (the downstream) | `operation ∈ {insert, update, upsert, delete}` |
| **Referenced step** (the upstream) | `operation ∈ {query, queryAll}` |

Additionally, the referenced step's `sequence` must be **strictly less than**
the referencing step's `sequence` — forward references are rejected.

DML→DML chains (consuming `success` / `error` output from a prior DML step)
are **out of scope** for v1. Tracked as a separate follow-up epic.

### Mutual exclusion (D4)

When `input_from_step_id` is set:

- `csv_file_pattern` **must be** `NULL`.
- `input_connection_id` **must be** `NULL`.

Validated at the Pydantic schema layer and again at the service layer against
the merged effective state (partial updates may clear one field while leaving
others on the row).

### Same-plan constraint

The referenced step must belong to the **same plan** as the referencing step.
Cross-plan references are rejected with 422.

### Reorder-time validation (D5d)

`reorder_steps` rejects with 422 if the new ordering would make any existing
`input_from_step_id` invalid — i.e. the referenced step would no longer
strictly precede the referencing step in the new sequence.

The error message lists the offending pair so the user knows what to clear
before reordering. Silent auto-clearing is rejected (too easy to lose
configuration without noticing).

### Plan duplication remap (D9)

`duplicate_plan` rewrites `input_from_step_id` from old step UUIDs to new
step UUIDs so the cloned plan's chain remains intact.

Note: the pre-existing field-drop bugs in `duplicate_plan`
(`output_connection_id`, `consecutive_failure_threshold`, `soql`,
`input_connection_id`) were fixed separately in SFBL-260.

---

## Resolver contract (D5)

Module: `app/services/step_reference_resolver.py`

```python
async def resolve_step_input(
    step: LoadStep,
    run_id: str,
    plan: LoadPlan,
    db: AsyncSession,
) -> tuple[BaseInputStorage, list[str]]:
    ...
```

**Behaviour:**

1. Looks up the upstream step's `JobRecord` for the current run
   (`partition_index=0`, because a query step produces a single CSV).
2. Reads `success_file_path` from that record.
3. Determines the storage backend from `plan.output_connection_id`:

   | `plan.output_connection_id` | Backend |
   |---|---|
   | `None` (local disk) | `LocalInputStorage(settings.output_dir)` — `rel_paths = [job_record.success_file_path]` (already relative) |
   | Set (S3 connection) | Parse the persisted `s3://bucket/full_key` URI → `S3InputStorage(bucket=bucket, root_prefix="", ...)` — `rel_paths = [full_key]` |

4. Returns `(storage, rel_paths)` — a singleton path list.

**S3 URI semantics (D5, review amendment 3):**

The S3 output sink writes an `s3://bucket/{root_prefix + key}` URI to
`JobRecord.success_file_path`. `S3InputStorage` only accepts source-relative
paths (it prepends its own `root_prefix`). The resolver therefore:

- Parses the URI to extract `(bucket, full_key)`.
- Constructs `S3InputStorage` with `root_prefix=""` so `full_key` is used
  as-is — this sidesteps needing to know the original `root_prefix` even if
  the `InputConnection`'s `root_prefix` is later changed.
- If the URI cannot be parsed, or if the bucket disagrees with the connection's
  configured bucket, raises `StepReferenceResolutionError`.

**Error class (D5b):**

`StepReferenceResolutionError` subclasses `InputStorageError`. The existing
`run_coordinator._execute_run_body` catch maps `InputStorageError` →
`OutcomeCode.STORAGE_ERROR` and marks the run `failed`. No exception-handling
changes are needed in the orchestrator.

**Raises** `StepReferenceResolutionError` when:
- The upstream `JobRecord` is missing for the current run.
- `success_file_path` is `NULL` or empty (the upstream step didn't write a result).
- The persisted URI is malformed or points to an unexpected bucket (S3 case).

---

## Wire-in point (D6)

In `step_executor._execute_step`, immediately before the existing DML input
discovery:

```python
if step.input_from_step_id:
    storage, rel_paths = await resolve_step_input(step, run_id, plan, db)
else:
    storage = await _get_storage(step.input_connection_id, db)
    rel_paths = storage.discover_files(step.csv_file_pattern)
```

The subsequent partitioning and dispatch logic is identical in both branches.

---

## Retry behaviour (D7)

`execute_retry_run` replays pre-built partitions from saved `JobRecord.csv_path`
values. The resolver runs **once per fresh run** and is never called on retry.
The downstream DML step's retry therefore replays the saved partition paths
without re-running the upstream query.

---

## Column contract (D8)

v1 contract (field projection deferred):

> The downstream operation's required Bulk API columns must appear in the
> upstream SOQL `SELECT` list.

Concretely:

| Operation | Required column(s) |
|---|---|
| `delete` | `Id` |
| `update` | `Id` + all fields being updated |
| `upsert` | External ID field + all fields being upserted |
| `insert` | All fields being inserted (no `Id`) |

The loader does **not** project or rename columns at reference time — what the
upstream query selects is what the downstream step receives. If the upstream
SOQL omits `Id` and the downstream step is a `delete`, the Bulk API will
reject the job at upload time with a field error.

Field projection / column rename / filter at reference time is tracked as a
follow-up epic.

---

## Observability (D10)

### New event

`StepEvent.INPUT_RESOLVED_FROM_STEP = "step.input.resolved_from_step"`

Emitted by the resolver after a successful resolution, carrying:

| Extra field | Value |
|---|---|
| `event_name` | `"step.input.resolved_from_step"` |
| `upstream_step_id` | UUID of the upstream step |
| `upstream_step_name_or_label` | `name` if set, else the computed `Step {seq}: {op} {obj}` label |
| `resolved_path` | The `rel_path` returned to the executor |
| `provider` | `"local"` or `"s3"` |

### Augmented step start

`StepEvent.STARTED` gains `input_from_step_id` in `extra={}` when the step
uses a step reference.

### No new metric / span

Resolution is brief and lives inside `step.execute` — a dedicated metric or
span adds low value at run cardinality.

---

## UI (D11)

Changes shipped in SFBL-264 (`StepEditorModal`, `StepList`, `planEditorUtils.ts`):

- **Step name input** — new optional text field at the top of the step editor;
  auto-fills a slug-style default; normalised (trim + empty→null) by the
  schema layer.
- **Input source widget** — three-way toggle:
  1. `Input connection / CSV file` (existing behaviour)
  2. `Local output (prior run)` (SFBL-178, unchanged)
  3. `From upstream step in this run` (new)
- When `From upstream step` is selected → dropdown listing preceding query
  steps in the plan. Disables `csv_file_pattern` and `input_connection_id`
  controls.
- Inline help when the dropdown would be empty: "Add a query step before this
  one to enable chaining."
- `StepList` renders a small chevron/arrow badge on chained steps showing the
  upstream step's name or label.

---

## Out-of-scope follow-ups

| Feature | Status |
|---|---|
| DML→DML chains (consume `success`/`error` from a prior DML step) | Separate follow-up epic |
| Field projection / column rename / filter at reference time | Separate follow-up epic |
| Runtime SOQL templating (`WHERE Id IN :upstream.Id`) | Separate feature |

---

## Story breakdown

| Story | Ticket | Scope |
|---|---|---|
| S1 — Schema & validation | SFBL-261 | Migration, model, schemas, service validation, name normalization, reorder-time validation, duplicate_plan remap |
| S2 — Resolver | SFBL-262 | `step_reference_resolver.py`, `StepReferenceResolutionError`, unit tests |
| S3 — Orchestrator wire-in | SFBL-263 | Branch in `step_executor`, `step.input.resolved_from_step` event, integration tests |
| S4 — UI | SFBL-264 | `StepEditorModal` name input + three-way input widget, `StepList` badge, frontend tests |
| S5 — Docs | SFBL-265 | This spec, `docs/usage/chaining-steps.md`, updates to `docs/usage/load-plans.md` |
