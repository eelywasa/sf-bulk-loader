# CSV → Salesforce Field Mapping

**Status:** Live spec — implementation tickets created in Jira (epic
**SFBL-301**, children **SFBL-304..315**, follow-up epics **SFBL-302**
and **SFBL-303**). Spec moves to `docs/specs/implemented/` once the
v1 epic PR merges.

---

## Background

Today the orchestrator and Bulk API 2.0 client treat the CSV header row as
authoritative: each column header **must** be a valid Salesforce API field
name on the target SObject, including any required external-ID relationship
syntax (e.g. `Account__r.External_Id__c` for non-polymorphic lookups, or
`User:Owner.Username` for polymorphic). Operators have to massage their
source files before loading — renaming columns, deleting columns the
target doesn't accept, and hand-crafting relationship headers for upserts.

This is the friction point a Salesforce Data Loader user expects the tool to
solve. The Data Loader presents a mapping grid that:

- lists the SObject's fields on one side and the CSV columns on the other,
- auto-maps where the names match,
- lets the user pick a destination field per CSV column (or skip it),
- supports relationship lookups (`Account:Name`-style),
- counts mapped vs. unmapped fields,
- saves/loads the mapping as an `.sdl` file.

Today the closest thing we have is the **External ID** combobox on upsert
steps, which is populated from the live CSV header row but does not solve
the broader mapping problem.

This spec scopes out a first-class mapping feature on the step editor, plus
extension stories (mapping persistence, static-value injection).

---

## Goals

1. **Decouple CSV column names from Salesforce API names.** The orchestrator
   should be able to load a CSV whose headers don't match the target schema,
   provided a mapping is configured on the step.
2. **Auto-map by exact name match.** Where a CSV column already matches a
   field API name (case-insensitive, optional whitespace tolerance), it
   should map by default.
3. **Surface the live SObject schema in the editor.** Pull the field list,
   types, and relationship metadata from the org so the user can pick from a
   real list, not type free-text.
4. **Support cross-object lookups for upserts.** `Account.ExternalId__c`-style
   relationship targeting is a first-class destination, not a workaround.
5. **Show mapped / unmapped counts** as the user edits, so they know when
   the step is shippable.

## Non-goals (this epic)

- Transformation / coercion expressions on values (uppercase, date parsing,
  regex, etc.). Mappings are 1:1 column-to-field; transformations are out
  of scope.
- Conditional mappings (`IF column-X THEN field-A ELSE field-B`).
- Multi-CSV mapping (joining two files). Each step is still one input file
  pattern → one SObject.
- Query-step output column mapping. Query steps emit whatever SOQL selects;
  this spec covers DML only.

---

## Locked design decisions

> All decisions in this section are **Locked** as of the spec Q&A pass on
> 2026-05-10. Revisions require a follow-up note in the changelog at the
> end of this file.

### D1 — Where the mapping lives (Locked)

A new table `load_step_field_mapping` keyed by `load_step_id`. Each
row encodes a **decision** the user has made about the step's mapping;
the `kind` column distinguishes the four decision types (data
movements vs. acknowledgements):

```sql
CREATE TABLE load_step_field_mapping (
    id              VARCHAR(36) PRIMARY KEY,
    load_step_id    VARCHAR(36) NOT NULL
                    REFERENCES load_step(id) ON DELETE CASCADE,
    -- Decision type:
    --   'column_to_field' — copy values from csv_column → sf_field
    --   'static_to_field' — write static_value → sf_field for every row
    --   'skip_column'     — explicitly do not map csv_column
    --                       (distinct from "no row exists" / never-touched)
    --   'ack_required'    — acknowledge a required sf_field is intentionally
    --                       not mapped (relies on SF default / trigger)
    kind            VARCHAR(20) NOT NULL,
    -- Source CSV column.  NOT NULL for column_to_field and skip_column;
    -- NULL for static_to_field and ack_required.
    csv_column      VARCHAR(255) NULL,
    -- Destination SF field (raw Bulk API CSV header form for relationship
    -- lookups — see D3).  NOT NULL for column_to_field, static_to_field,
    -- and ack_required; NULL for skip_column.
    sf_field        VARCHAR(255) NULL,
    -- Used only when kind = 'static_to_field'; NULL otherwise.
    static_value    TEXT NULL,
    -- Free-text user note attached to skip / ack decisions; NULL for
    -- data-movement rows.
    note            TEXT NULL,
    sequence        INTEGER NOT NULL,  -- preserves UI ordering
    created_at      TIMESTAMP NOT NULL DEFAULT now(),
    updated_at      TIMESTAMP NOT NULL DEFAULT now(),

    CHECK (kind IN ('column_to_field','static_to_field','skip_column','ack_required')),
    CHECK (
        (kind = 'column_to_field' AND csv_column IS NOT NULL AND sf_field IS NOT NULL AND static_value IS NULL)
     OR (kind = 'static_to_field' AND csv_column IS NULL     AND sf_field IS NOT NULL AND static_value IS NOT NULL)
     OR (kind = 'skip_column'     AND csv_column IS NOT NULL AND sf_field IS NULL)
     OR (kind = 'ack_required'    AND csv_column IS NULL     AND sf_field IS NOT NULL AND static_value IS NULL)
    )
);

-- One destination at most per step (across data-movement rows; ack rows
-- target the same sf_field by definition, so include them too).
CREATE UNIQUE INDEX uq_step_field_mapping_dest
    ON load_step_field_mapping(load_step_id, sf_field)
    WHERE sf_field IS NOT NULL;

-- One decision at most per CSV column (a column is either mapped or
-- explicitly skipped, not both).
CREATE UNIQUE INDEX uq_step_field_mapping_csv_col
    ON load_step_field_mapping(load_step_id, csv_column)
    WHERE csv_column IS NOT NULL;
```

**Source-side cardinality.** The unique index on `csv_column` enforces
1:1 mapping by default — one CSV column can target one SF destination,
or be skipped, but not both. Broadcasting a single CSV column to
multiple SF fields is a v2 concern; if a real user case lands, lift
the index and re-evaluate. Calling this out explicitly because the
original spec only constrained destinations and left source cardinality
ambiguous.

Rationale for a separate table (vs. JSON column on `LoadStep`):

- The mapping list grows linearly with the SObject's field count (often
  100+). A normalised table keeps row size bounded and makes the schema
  introspectable.
- Per-row `created_at` / `updated_at` lets us surface staleness when the
  org's schema drifts.
- It mirrors the shape we'd want for future per-field metadata (e.g. a
  "reviewed by user" flag for auto-mapped rows).

**Empty mapping = pass-through.** When a step has zero rows in
`load_step_field_mapping`, the orchestrator behaves exactly as it does
today: CSV headers are used verbatim. This preserves backwards
compatibility with every existing plan.

**New steps land empty.** When a DML step is created, no mapping rows
are pre-populated, even if the `object_name` and CSV pattern are both
resolvable. The user opens the "Field mapping" disclosure in the editor
to opt in; expanding the panel triggers the describe fetch and the
auto-map pass (D4). This avoids a side-effect on step-create that would
fire a Salesforce HTTP call as the user types in the object name field.

### D2 — Salesforce metadata fetch (Locked)

Add `GET /api/connections/{id}/objects/{sobject}/describe` →

```json
{
  "name": "Account",
  "fields": [
    {
      "api_name": "Name",
      "label": "Account Name",
      "type": "string",
      "createable": true,
      "updateable": true,
      "nillable": false,
      "defaulted_on_create": false,
      "external_id": false,
      "id_lookup": false,
      "reference_to": [],          // empty unless type === "reference"
      "relationship_name": null    // populated for reference fields
    },
    {
      "api_name": "OwnerId",
      "label": "Owner ID",
      "type": "reference",
      "createable": true,
      "updateable": true,
      "nillable": false,
      "defaulted_on_create": true,
      "external_id": false,
      "id_lookup": false,
      "reference_to": ["User", "Group"],
      "relationship_name": "Owner"
    }
  ],
  "child_relationships": [          // only those targetable via external IDs
    { "field": "AccountId", "child_sobject": "Contact" }
  ],
  "fetched_at": "2026-05-10T12:34:56Z"
}
```

Backed by `/services/data/{api}/sobjects/{name}/describe`, which is the same
shape `connections.list_connection_objects` already uses for the SObject
list. Shape this thinly — we only forward the fields the UI needs, but keep
`fetched_at` so the client can display staleness.

**Field-flag inventory.** Every flag the locked decisions depend on must
appear in the response shape — adding them as an afterthought during
implementation is how validation logic ends up wrong:

| Flag | Used by | Notes |
|------|---------|-------|
| `createable` / `updateable` | D5 (required-field check), filter-on-pick | Suppresses non-writable system fields from the destination picker |
| `nillable` | D5 | Required-field denominator |
| `defaulted_on_create` | D5 | A required-looking field that Salesforce auto-fills on insert (e.g. `OwnerId`) does not need a mapping or an `ack_required` row |
| `external_id` | D3 | Filter for the parent-side external-ID picker |
| `id_lookup` | D3 | Salesforce flags fields usable as lookup resolvers via `idLookup` rather than `external_id`; the parent-side picker should accept either flag |
| `reference_to` / `relationship_name` | D3 | Source-of-truth for the Bulk API relationship-header construction |

**Parent-object metadata for cross-object lookups.** The describe payload
above describes the *target* SObject only. To populate the parent-side
external-ID picker (D3), the UI must call the same endpoint a second
time once the user picks a parent SObject — e.g. picking *User* for
`OwnerId` triggers `GET /api/connections/{id}/objects/User/describe`,
the response is filtered to fields where `external_id || id_lookup` is
true, and that list feeds the second combobox.

This **lazy parent-fetch** approach keeps the initial payload small (a
mature `Account.describe()` is already 100+ KB) and avoids prefetching
parent describes the user might never open. The cache layer below
applies uniformly — most parent describes are popular (`User`,
`Account`, `Contact`) so cache hit rate stays high. The only downside
is one extra round-trip the first time a user opens a polymorphic
picker against a fresh cache, which is acceptable.

If we later discover users frequently bounce through every parent type,
we can opt into an `?expand=reference_targets` mode that bundles
external-ID fields for each `reference_to[]` entry. Out of scope for
v1.

**Cache layer.** Describe responses are stable enough to cache for the life
of a connection's access token (≥ several minutes) and large enough that
re-fetching on every editor open is wasteful. v1: in-process LRU keyed by
`(connection_id, sobject)` with a configurable TTL (default 10 min). v2
could promote this to the DB if multi-replica deployments materialise.

The cache is keyed **per `connection_id`**, not per org. Two `Connection`
rows pointing at the same Salesforce org may run as different users with
different field-level visibility, so a shared per-org cache would leak
fields the running user can't actually see. The redundancy of fetching
the same describe twice for overlapping connections is bounded (10-min
TTL, lazy on panel-open) and acceptable for v1.

**Freshness UX.** The dominant staleness case is *new* fields appearing
(either freshly added by an admin, or freshly visible because permissions
were granted to the running user) — a stale cache that hides those is
worse than one that retains a removed field. So:

- The mapping panel header always shows a *"Schema as of HH:MM · X
  minutes ago"* line with a **Refresh schema** button next to it.
- Expanding the mapping panel auto-refetches the describe if the cache
  entry is older than ~60 s. In practice most panel opens hit the live
  org without the user thinking about it; the cache acts as a debouncer
  for closing/reopening the modal in quick succession.
- Saving the step does **not** re-fetch — the user has already finished
  picking destinations from a fresh-on-open list, and re-fetching at
  save time would either reject work just done or accept it without the
  new options ever appearing. Drift between save and run is caught at
  preflight (D5 / wave 2 story).

### D3 — Cross-object lookup syntax (Locked)

Bulk API 2.0 CSV relationship-lookup headers come in two forms,
depending on whether the reference field is polymorphic:

- **Non-polymorphic:** `{RelationshipName}.{IndexedFieldName}` — e.g.
  `Account__r.External_Id__c` to upsert a Contact's `Account__c` lookup
  by an external ID on Account, or `Owner.Username` (single-target
  variant). The relationship name is the `relationship_name` from
  describe, **not** the lookup field's API name.
- **Polymorphic:** `{ParentObjectType}:{RelationshipName}.{IndexedFieldName}`
  — e.g. `User:Owner.Username` for `OwnerId`, or
  `Contact:Who.Email` for `WhoId`. The leading `{ParentObjectType}:`
  is the qualifier that tells the Bulk API which member of the
  polymorphic reference to resolve against.

The "indexed field" must be a parent-side field flagged
`external_id == true` **or** `id_lookup == true` (per D2's flag
inventory). Salesforce uses both flags to mark fields usable as upsert
resolvers — `Username` on User, for example, is `id_lookup` rather
than `external_id`.

The describe payload above gives us everything needed to construct
either form: the lookup field's `relationship_name`, its
`reference_to[]`, and the parent-side describe (lazily fetched) for
the indexed-field list.

UI flow for relationship destinations:

1. User clicks a row's "Destination" cell and chooses **Lookup via external
   ID…** (option below the flat field list).
2. A two-step picker appears: parent SObject (from `reference_to[]`) →
   indexed field on that parent (filtered to `external_id || id_lookup`).
3. The mapping persists the rendered header form; the UI displays it as a
   pretty breadcrumb (`Owner ▸ Username`).

**Polymorphic references** (`OwnerId` → User|Group, `WhoId` → Contact|Lead)
use the same two-step picker. The persisted header always carries the
`{ParentObjectType}:` qualifier for these, even when the user picks
the dominant parent — being explicit avoids ambiguity at load time.
For non-polymorphic references (single-element `reference_to[]`) the
qualifier is omitted and the parent-pick step is auto-completed. The
first step (parent SObject) makes the load-time semantic choice
explicit: picking `User.Username` versus `Group.DeveloperName` for
`OwnerId` changes which records the lookup will resolve against, and
surfacing that as its own step encourages the user to think about it.
A flat list would also balloon on orgs with custom external IDs on
multiple parent types.

> **Original spec error.** An earlier draft listed the example
> `Account.ExternalId__r:Account__c` and described the syntax as
> `{Relationship}.{ExternalIdField}:{LookupFieldApiName}` — that is
> incorrect (it would generate invalid Bulk API headers). The forms
> above are what Salesforce actually documents. Keeping this note for
> implementer awareness; tests must assert against the corrected
> shape.

### D4 — Auto-map rules (Locked)

On open of the mapping panel, for each CSV header `h`:

1. **Exact match** (case-insensitive after trim) against any field's
   `api_name` → auto-map.
2. **Label match** (case-insensitive after trim) against any field's
   `label` → auto-map, but flag with a low-confidence indicator so the user
   can sanity-check (labels can collide).
3. **Suffix `__c` tolerance** — `account_id` matches `Account_id__c`
   (custom field convention).
4. Otherwise — leave unmapped, surface in the unmapped count.

The auto-map runs **once** when the mapping is first generated and any time
the user clicks **Re-auto-map**. It does not silently overwrite manual
edits.

### D5 — Required-field validation (Locked)

Steps with at least one mapping row become subject to a **completeness**
check at save time. Rules vary by operation:

**`insert`**

- Every field where `createable == true && nillable == false &&
  defaulted_on_create == false` must have either a `column_to_field`,
  `static_to_field`, or `ack_required` row in the mapping. Fields
  where `defaulted_on_create == true` (e.g. `OwnerId`) are exempt —
  Salesforce auto-populates them, and forcing the user to map or
  acknowledge them is noise.
- The `ack_required` decision type is the explicit "I know this is
  required, I'm letting a trigger / workflow fill it" escape hatch
  the Data Loader exposes as a checkbox; here it's a persisted row
  (per D1) so the next time the panel opens the acknowledgement is
  remembered.

**`update`**

- The mapping **must** include `Id` as a destination (via
  `column_to_field` — there's no sensible static or ack form for it).
  Bulk API update fails the whole job without an Id column. The save-
  time validator checks for this explicitly because `Id` is not
  flagged as required by describe (`nillable == true` on most
  SObjects' Id fields).
- Other createable-but-not-nillable fields are *not* required for
  update; they're enforced by Salesforce only on insert.

**`upsert`**

- Same insert rules apply (createable + non-nillable + not defaulted).
- **Plus**: the configured external-ID field (`LoadStep.external_id_field`)
  must appear as a destination via a `column_to_field` (or
  `static_to_field`) row whose `sf_field` is **exactly** the literal
  API name in `external_id_field` — e.g. `External_Id__c`.
- The relationship-header form from D3 (`Account__r.External_Id__c`,
  `User:Owner.Username`) is **not** a valid satisfier for this rule.
  Those headers resolve **parent lookups** to a related record via an
  external ID; they do not identify the upsert key itself.
  `externalIdFieldName` in the Bulk API job payload is a field on the
  **target** SObject (the one being upserted), and the matching CSV
  column header must be the bare field API name. The validator must
  reject a relationship-header destination as a satisfier even if its
  trailing indexed-field happens to match `external_id_field`.

> Worth flagging because the assumption is intuitive: relationship
> headers and the upsert key both involve external IDs, and an earlier
> draft of this spec mistakenly allowed the relationship form here. A
> test case should assert that a step with
> `external_id_field = "External_Id__c"` and a sole mapping row
> `Account__r.External_Id__c → ...` is **rejected**.

**`delete`**

- Single rule: the one mapping row must target `Id`. Enforced by D8's
  short-circuit UI before validation runs.

**Cross-cutting**

- All operations: every `column_to_field` / `skip_column` row must
  reference a CSV column that actually exists in the resolved input
  CSV's header row. Caught at preflight (wave 2) since the input
  pattern may not be resolvable at save time.
- Validation surfaces as inline errors on the mapping panel and as a
  blocking error in `stepFormErrors`.

### D6 — Orchestrator integration (Locked)

`csv_processor.partition_csv` is the single place the **source** input
CSV becomes Bulk API bytes. Mapping is applied as a header-rewrite +
column-projection + optional static-injection step **before**
partitioning:

- **Read** the source header row and resolve column indices for every
  `column_to_field` / `skip_column` row.
- For each `column_to_field` row, in `sequence` order: emit the
  configured `sf_field` as the output header at the next slot, copying
  values from the source column at the resolved index.
- For each `static_to_field` row, in `sequence` order: emit `sf_field`
  as a header and write the configured `static_value` for every data
  row.
- `skip_column` rows are no-ops in the rewriter — they exist only to
  persist user intent; they read like "no mapping row" at execution
  time.
- `ack_required` rows are also no-ops at the rewriter level; they
  contribute only to D5 validation.
- Source columns without any decision row (neither mapped nor
  explicitly skipped) are dropped. Mapping rows whose `csv_column` is
  missing from the source header fail loud at step start (job aborts
  before any partition is uploaded).

The transformation is a streaming pass — no whole-file buffering.
Output partitions remain UTF-8 / LF as today.

The rewrite lives in a new helper `apply_mapping(reader, mapping) ->
Iterator[list[str]]`, used by `partition_csv` only.

**Signature contract — how mapping reaches `partition_csv`.** Today's
signature is `partition_csv(source, partition_size, *, encoding=None)`
and call sites in [step_executor.py](../../backend/app/services/step_executor.py)
and the Track B path of `build_retry_partitions` pass only the file
handle and partition size. The new signature adds an optional
mapping argument:

```python
def partition_csv(
    source: Union[pathlib.Path, IO[str]],
    partition_size: int,
    *,
    encoding: Optional[str] = None,
    mapping: Optional[Sequence[FieldMappingRow]] = None,
) -> Iterator[bytes]:
```

When `mapping is None` or empty, behaviour is byte-for-byte identical
to today (pass-through). When non-empty, `apply_mapping` runs as
the streaming rewrite pass before partitioning.

**Caller responsibilities** — both call sites must load mapping rows
from the database and pass them in:

- [`step_executor.run_step`](../../backend/app/services/step_executor.py)
  fetches the step's `load_step_field_mapping` rows once (alongside
  the step itself) and passes them via the `mapping=` kwarg on each
  `_partition(...)` call. The DI seam (`_partition` is the injected
  `partition_csv`) is preserved.
- `build_retry_partitions` Track B does the same when re-discovering
  the original source file. Track A continues to pass through
  unchanged (per spec — Track A bytes are already destination-form).

This explicit contract is locked in as part of SFBL-307 so that any
future caller of `partition_csv` knows up-front that mapping is the
caller's responsibility, not magic. A unit test asserts that calling
the function without `mapping=` produces the same output as today's
pass-through behaviour for every existing fixture.

**Retry-path semantics (this is the trap).** `build_retry_partitions`
(`csv_processor.py`) handles two distinct tracks for failed/aborted
jobs, and they need different mapping treatment:

- **Track A — Salesforce result-file replay.** Reads the run's
  `error_file_path` / `unprocessed_file_path` artefacts. After
  stripping the leading `sf__Id` / `sf__Error` columns, the remaining
  header row already contains the **destination** (post-mapping) Bulk
  API headers — that's literally what we uploaded. **`apply_mapping`
  must not run on Track A**: re-applying a `csv_column → sf_field`
  rewrite would look up source-side header names that no longer exist
  and abort the retry. Track A bytes pass through to the partitioner
  unchanged.
- **Track B — re-discover original source CSV.** Reads the original
  input CSV via the step's glob and partition index, then calls
  `partition_csv(fh, partition_size)` directly. Because Track B goes
  through `partition_csv`, mapping flows through automatically — no
  additional wiring needed.

Concretely: only `partition_csv` invokes `apply_mapping`.
`build_retry_partitions` does not call `apply_mapping` directly; Track
A skips it by design, Track B inherits it transitively.

A regression test must lock this in by retrying a step whose error
file's headers contain a relationship lookup (e.g.
`User:Owner.Username`) and asserting the retry partition matches the
result file's bytes — i.e. no second rewrite happened.

#### Header discovery (per input source mode)

The mapping panel's auto-map and the preflight check both need to know
the *source* CSV's header columns. Today's UI only previews literal
file paths, but the step's input source covers four shapes — each
needs a defined behaviour:

| Mode | Auto-map source for headers | Preflight |
|------|----------------------------|-----------|
| Literal local CSV path | Read the header row from that file (today's behaviour). | Validate that file's header against mapping rows. |
| Glob pattern → N matched local files | Read headers from the **first** matched file (alphabetical by resolved path) — fast, deterministic. The user accepts that all glob-matched files must share compatible columns. | Validate **every** matched file's header against mapping rows; surface the union of missing-column errors. Do not stop at the first failure. |
| Input connection (S3 / remote) with literal or glob | Same as the local cases, via the connection's `storage` adapter (`storage.discover_files()` + `storage.open_text()`). The existing CSV preview path already abstracts this; mapping reuses it. | Same — every matched object's header is validated. |
| `input_from_step_id` (chained query output) | **Out of scope for v1.** Mapping panels on `from_step` mode steps fall back to pass-through; the disclosure is hidden with a note: *"Mapping is not available when input is chained from an upstream query step. Contact the spec maintainer if you need this."* The upstream step's output structure is governed by SOQL projection logic that does not yet exist as a reusable helper. Tracked as a future epic; not a child of SFBL-301. |

**Why glob mode validates every file at preflight (not just the
preview path).** Today's preflight short-circuits to the literal-path
preview for performance. With explicit mapping rows, a single
non-conforming file in a 50-file glob is enough to fail every
partition derived from it — so silent skip-validation would defeat
the purpose. Reading 50 header rows is cheap (one CSV reader, one
`next(reader)` per file); the cost is bounded by file count, not file
size.

**Glob mode + the auto-map UI.** The "first matched file" rule for
header discovery is good enough for auto-map suggestions, but the
panel should also show a small *"Headers from `accounts_001.csv` (1
of 50 matched files)"* line so the user knows which file fed the
column list. A subsequent **Re-scan headers** action (Wave 2 follow-up
if needed; not blocking v1) could compute the *union* of all matched
files' headers and flag columns that exist in only some files.

### D7 — Counts UI (Locked)

A header strip above the mapping table shows:

```
12 of 18 CSV columns mapped · 4 unmapped · 2 skipped · 5 of 7 required SF fields covered
```

- "Skipped" = CSV columns with a `skip_column` row (the user explicitly
  chose not to map). Distinct from "unmapped" — a CSV column with no
  mapping row at all, never auto-matched, never decided.
- The required-field denominator is the count from D5; the numerator
  includes both data-movement rows (`column_to_field`, `static_to_field`)
  *and* `ack_required` rows. The strip should subdivide the latter so
  the user sees how many required fields are covered by mapping vs.
  acknowledgement, e.g. *"5 of 7 required fields covered (3 mapped,
  2 acknowledged)"*.

Empty mapping (pass-through) shows a single banner: *"No explicit mapping
— CSV headers used verbatim. [Configure mapping]"*.

### D8 — Delete-step UI short-circuit (Locked)

`delete` operations need exactly one CSV column → `Id`. Rendering the
full mapping grid for a step type with one valid destination is
busy-work, so the editor renders a **single combobox** for delete steps:
*"Which CSV column holds the record Id?"* with the resolved CSV header
list as options. No SObject describe is needed (Id is universal), no
required-field validation runs, and no relationship picker is offered.

Persistence is unchanged: a single `load_step_field_mapping` row is
written under the hood (`kind = 'column_to_field', csv_column = <user
pick>, sf_field = 'Id'`), keeping the data model uniform across
operations. Only the editor UI differs. This mirrors the pattern users
already know from the existing External ID combobox on upsert steps.

> **Why no delete-by-external-ID path?** Bulk API 2.0 explicitly
> rejects this combination. Per the Salesforce docs:
>
> > "Bulk deletion requests can include only the `Id` field. This
> > behavior is different from other requests in Bulk API 2.0."
> > — *Marketing Cloud / Bulk API 2.0, Manage Records — Delete*
>
> Although our existing
> [`SalesforceBulkClient.create_job`](../../backend/app/services/salesforce_bulk.py)
> would happily forward `externalIdFieldName` for any operation, the
> Salesforce server will fail the job with a validation error. So:
>
> - The mapping panel must **not** offer a "Lookup via external ID"
>   destination for delete steps.
> - The save-time validator must reject any delete-step mapping whose
>   sole row's `sf_field` is anything other than `Id`.
> - The Bulk API client should also defensively refuse to send
>   `externalIdFieldName` when `operation == 'delete'`, with a clear
>   error message — preferable to a 400 from Salesforce.
>
> Worth retaining the note here because parity-with-upsert is the
> intuitive assumption (since the create-job payload accepts the
> field uniformly) and the asymmetry is easy to miss.

---

## UI implementation guardrails

The mapping panel must be assembled from the existing design system in
[`frontend/src/components/ui/`](../../frontend/src/components/ui/) and
the form-style constants in
[`frontend/src/components/ui/formStyles.ts`](../../frontend/src/components/ui/formStyles.ts).
Read [`docs/ui-conventions.md`](../ui-conventions.md) before starting
any of the wave-3 stories — it is the authority on tokens, form
patterns, and shared components.

### Required component reuse

| Surface | Reuse |
|---------|-------|
| Mapping panel container | `Modal` (already used by `StepEditorModal`) — open the panel as a section *inside* the existing step modal, not a nested modal |
| Mapping grid | **`DataTable`** from `components/ui/DataTable.tsx` — see the "DataTable vs CsvPreviewPanel" section in `ui-conventions.md`. Do NOT roll a bespoke `<table>` |
| Per-row destination picker (SF field) | `ComboInput` (same component used today for `external_id_field`) |
| Cross-object lookup picker (D3) | Two stacked `ComboInput`s — parent SObject, then external-ID field; do not introduce a new picker primitive |
| Refresh schema / Re-auto-map / Configure mapping | `Button` with `variant="secondary" size="sm"` — match the existing Validate SOQL button styling |
| "No explicit mapping" pass-through banner | `EmptyState` |
| Schema-loading state | `Spinner` (centered) using the loading pattern in `ui-conventions.md` § "Loading states" |
| Mapped / unmapped / skipped tallies (D7) | `Badge` for the per-row status pip; flat text in the header strip with token-driven colours |
| Inline errors & alerts | `ALERT_ERROR` / `ALERT_SUCCESS` constants from `formStyles.ts`; helper text uses `HELPER_TEXT_CLASS` |
| Field labels and required markers | `LABEL_CLASS` + `RequiredAsterisk` |
| Inputs, selects, textareas | `INPUT_CLASS` / `SELECT_CLASS` / `TEXTAREA_CLASS` — never hand-rolled |

### Tokens, not raw colours

- All surfaces, borders, text, and accents must use the design tokens
  documented in `ui-conventions.md` § "Token quick reference"
  (`bg-surface-*`, `text-content-*`, `border-border-*`, `text-accent`,
  `text-state-*`). No `gray-*`, `slate-*`, or hex literals.
- The status colours for mapping rows (auto-mapped / manual / required-
  unmet / skipped) must compose from the existing state tokens
  (`state-success`, `state-warning`, `state-error`, `state-info`). If a
  new semantic state genuinely doesn't fit any existing token, propose
  a new token through the `ui-conventions.md` change process **before**
  writing the component.

### `ui-conventions.md` is part of the task, not a follow-up

Per the project's documentation policy
(`CLAUDE.md` → "UI conventions (frontend)"), any new shared pattern
introduced by this epic must land in `ui-conventions.md` in the **same
PR** that introduces it. Concretely, the UI wave is expected to add at
least:

- A "Mapping grid pattern" section under "Form elements" or
  "Shared UI components", describing column layout, the per-row
  status pip, and the empty / pass-through state.
- A note under "Shared UI components" if a new helper component falls
  out of the work (e.g. a `MappingRow` row renderer); skip this if all
  reuse stays at the `DataTable` + `ComboInput` level.
- An entry under "Anti-patterns" if any tempting drift trap surfaces
  during build (e.g. "do not nest a second `Modal` inside the step
  editor for the mapping panel").

If the UI wave PR does not touch `ui-conventions.md`, it is not ready
to merge.

---

## Documentation impact

User-facing behaviour changes ship with their docs in the same PR
(`CLAUDE.md` → "Epic Definition of Done — documentation"). For this
epic the audit list is:

### New usage topic — `docs/usage/field-mapping.md`

A dedicated topic is warranted: the feature is large enough that
folding it into `load-plans.md` would bloat that file, and the help
shell is built from one-file-per-topic. Required frontmatter (per
`CLAUDE.md` → "Usage authoring contract"):

```yaml
---
title: Mapping CSV columns to Salesforce fields
slug: field-mapping            # NEVER renamed once shipped
nav_order: 45                  # between load-plans (40) and running-loads (50)
tags: [plans, mapping, csv]
required_permission: plans.manage  # must match a key in backend/app/auth/permissions.py
summary: >-
  Map CSV columns to Salesforce fields, configure cross-object lookups
  for upserts, and review mapped / unmapped counts before running.
---
```

Topic outline:

1. *What this covers / who should read this* — operators authoring DML
   steps; explicitly mention this is opt-in and the legacy "headers
   must match" behaviour still applies when no mapping is configured.
2. Opening the mapping panel; what auto-map does (D4).
3. Per-row controls: pick destination, skip, switch to static value
   (extension story — gated on Wave 4 ship date).
4. Cross-object lookups for upsert (D3) — walk through the two-step
   picker with a worked example like `Account__r.External_Id__c`
   (non-polymorphic) and the polymorphic
   `User:Owner.Username` form for `OwnerId`.
5. Polymorphic references (`OwnerId`, `WhoId`) — explain the parent-
   pick semantic.
6. Counts strip and required-field validation (D7 / D5).
7. Refreshing the schema (D2 freshness UX).
8. Delete-step short-circuit (D8).
9. *Related* — link to `csv-format.md`, `load-plans.md`, `bulk-query.md`,
   `chaining-steps.md`.

### Existing topics that need edits

| File | Change |
|------|--------|
| `docs/usage/csv-format.md` | The "Relationship notation (lookups by external ID)" section currently presents hand-rolled relationship headers as the only option. Add a sibling note saying mapping is now the recommended path and link to the new topic. The hand-rolled path remains valid (pass-through behaviour) but is no longer the primary instruction. |
| `docs/usage/load-plans.md` | In the step-authoring section, mention the optional mapping panel and link to the new topic. Do not duplicate the content — single sentence + link. |
| `docs/usage/running-loads.md` | If preflight gains a "mapping references missing CSV column" failure mode (D6 / wave 2), add it to the preflight error list. |
| `docs/usage/bulk-query.md` & `chaining-steps.md` | Cross-link from the "what the downstream step sees" sections to the new mapping topic, since chained DML steps will commonly want to remap columns coming out of a query. |
| `docs/usage/index.md` | Add the `field-mapping.md` row in the **Authoring** section (between `load-plans` at order 40 and `running-loads` at order 50, hence `nav_order: 45`). |

### In-app help build

Run `node frontend/scripts/check-help-links.mjs` locally before pushing
the UI wave PR (CI's `docs-drift` job enforces this anyway). Any anchor
referenced from another usage page or from the editor's "Configure
mapping" panel must resolve to a real heading in the new topic. Stable
anchor names matter — the editor will deep-link to specific sections
(e.g. "Cross-object lookups").

### `docs/architecture.md` and friends

The CSV-processor flow diagram at the top of
`docs/architecture/csv-pipeline.md` (or equivalent — confirm during
wave 1) needs a note that header rewrite + projection happens before
partitioning when a step has mapping rows. Keep it terse; the
authoritative description lives in this spec.

### `.env.example`

No new environment variables are introduced by the locked decisions.
If a story adds the describe-cache TTL as a setting, add the variable
to `.env.example` in the same PR.

### Spec lifecycle

Once all wave PRs merge and the feature is shipped, this spec moves to
`docs/specs/implemented/field-mapping-spec.md` with the standard
banner at the top, per the documentation policy.

---

## Story breakdown

Epic key: **SFBL-301**. All wave 1–3 tickets are children of this epic
in Jira. Wave 4 epics (W4-A static value, W4-B reusable templates +
`.sdl`) are tracked separately as **SFBL-302** and **SFBL-303**.

### Wave 1 — Backend foundations

- **SFBL-304** — Data model: `load_step_field_mapping` table with the
  four `kind` decision types per D1, Alembic migration, ORM model,
  Pydantic schemas, CRUD service, plus the partial unique indexes on
  `sf_field` and `csv_column`. CHECK constraints enforced. No API
  surface yet beyond what's needed for tests.
- **SFBL-305** — Plan duplication: extend `duplicate_plan` in
  `app.services.load_plan_service` to deep-copy each step's
  `load_step_field_mapping` rows, remapping `load_step_id` to the
  cloned step's UUID. Mirror the existing two-pass pattern used for
  `input_from_step_id`. Regression test: duplicate a plan whose steps
  carry mapping rows of every `kind`, assert row counts, content, and
  FK targets all match.
- **SFBL-306** — Describe endpoint and metadata cache: thin proxy with
  in-process LRU TTL cache. Response shape per D2's flag inventory
  (incl. `defaulted_on_create`, `id_lookup`). Test against a mock
  describe payload covering flat fields, references, polymorphic
  references, and `id_lookup`-only resolvers (e.g. User.Username).
- **SFBL-307** — `apply_mapping` helper in `csv_processor`, wired into
  `partition_csv` only (per D6 retry semantics — **not** into
  `build_retry_partitions` Track A). Pass-through behaviour preserved
  when the step has no mapping rows. Failure modes (missing CSV
  column, duplicate destination, `column_to_field` row referencing a
  non-existent source column) raise `CSVProcessorError` before any
  upload. Includes the regression test asserting Track A retry
  partitions match the source result file byte-for-byte (no re-rewrite).
  Folds in the Bulk-client guardrail rejecting `delete +
  externalIdFieldName` (D8).

### Wave 2 — API and orchestrator integration

- **SFBL-308** — Mapping CRUD endpoints under `/api/load-steps/{id}/mappings`
  (list, replace-all, single-row PATCH). RBAC reuses the load-step
  permissions.
- **SFBL-309** — Required-field completeness check (D5) wired into step
  save validation. Per-operation rules: `insert`/`upsert` use the
  `createable && !nillable && !defaulted_on_create` set; `update`
  requires `Id` as a destination; `delete` rejects any non-`Id`
  destination.
- **SFBL-310** — Step preflight: when a step has mappings, the preflight
  endpoint validates that every `csv_column` referenced (by
  `column_to_field` or `skip_column` rows) exists in the resolved
  CSV's header (uses the existing literal-pattern preview path).

### Wave 3 — UI

- **SFBL-311** — Mapping panel inside `StepEditorModal`: lazy-loaded when
  the user expands a "Field mapping" disclosure. Renders the mapping grid
  via the existing `DataTable` component (CSV column | SF field) with
  per-row destination `ComboInput`, skip toggle, and counts strip (D7).
  *DoD:* `ui-conventions.md` updated with the mapping-grid pattern;
  zero raw `gray-*`/hex colours; no nested `Modal`.
- **SFBL-312** — Auto-map rules (D4) and **Re-auto-map** action; manual
  edits are preserved. Includes the schema-freshness header strip (D2)
  and the **Refresh schema** button.
- **SFBL-313** — Cross-object lookup picker (D3): two-step `ComboInput`
  flow for `reference` fields. Polymorphic references (`OwnerId`,
  `WhoId`) reuse the same component path. Tests assert byte-exact
  persisted destination strings to lock in the corrected H1 syntax.
- **SFBL-314** — Delete-step UI short-circuit (D8): single `ComboInput`
  for the Id-column pick; full grid suppressed.
- **SFBL-315** — Documentation: new
  [`docs/usage/field-mapping.md`](../usage/field-mapping.md) topic with
  frontmatter per the authoring contract; cross-links added to
  `csv-format.md`, `load-plans.md`, `bulk-query.md`, `chaining-steps.md`,
  and `usage/index.md` (`nav_order: 45`). Run
  `node frontend/scripts/check-help-links.mjs` locally before opening
  the PR. *DoD:* `docs-drift` CI passes; deep-link anchors used by the
  editor's "Configure mapping" affordance resolve.

> **Wave 3 epic-PR DoD reminder.** Before opening the wave PR, audit
> the bullets in this spec's *UI implementation guardrails* and
> *Documentation impact* sections. A wave-3 PR that doesn't touch both
> `docs/ui-conventions.md` and `docs/usage/` is not ready to open.

### Wave 4 — Follow-up epics (NOT children of this epic)

Tracked as separate epics so they can be sequenced independently of v1.

#### W4-A — Static-value mapping (UI exposure)

The v1 schema already persists `kind = 'static_to_field'` rows (D1) and
`apply_mapping` already injects them at partition time (D6). This epic
adds the UI layer:

- A "Static value" toggle on each mapping row, swapping the source-side
  control from a CSV-column picker to a free-text value input.
- Validation: static value cannot be empty when the toggle is on; an
  empty static value is not a valid Bulk API CSV cell.
- Counts strip extension: distinguish *mapped (column)* from
  *mapped (static)* in the breakdown.

Standalone — no dependency on the templates epic.

#### W4-B — Reusable mapping templates with `.sdl` interop

The original spec sketched two separate follow-ups (a "save and load
mapping files" story and a "reusable mapping library" story). On
review these are the **same primitive at different levels of
fidelity**, and treating them as one epic with the internal model as
the primary abstraction is cleaner than bolting `.sdl` directly onto
the step's mapping rows.

**Internal model is the primary.** A `field_mapping_template` is a
named, scoped, reusable mapping definition independent of any
particular step:

```sql
CREATE TABLE field_mapping_template (
    id              VARCHAR(36) PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,
    sobject         VARCHAR(255) NOT NULL,
    -- NULL = global (any connection); else scoped to a connection
    connection_id   VARCHAR(36) NULL REFERENCES connection(id) ON DELETE CASCADE,
    -- Optional: which user/auth scope created/owns it
    created_by      VARCHAR(36) NULL REFERENCES "user"(id) ON DELETE SET NULL,
    description     TEXT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT now(),
    updated_at      TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE field_mapping_template_row (
    id                          VARCHAR(36) PRIMARY KEY,
    field_mapping_template_id   VARCHAR(36) NOT NULL
                                REFERENCES field_mapping_template(id) ON DELETE CASCADE,
    -- Same kind / csv_column / sf_field / static_value / note / sequence
    -- columns as load_step_field_mapping (D1).  Same CHECK constraint.
    -- Intentionally a parallel structure rather than reusing the existing
    -- table — templates are first-class, steps just adopt copies.
    ...
);
```

**Operations on templates** (the user's affordances):

- *Save current step's mapping as a template* — copy the step's
  `load_step_field_mapping` rows into a new `field_mapping_template`
  with whatever name/scope the user picks.
- *Apply a template to a step* — copy the template's rows into the
  step's mapping (overwriting or merging — UX TBD in the epic). This
  is a **copy**, not a reference: subsequent template edits don't
  retroactively change applied steps. Keeps the per-step mapping
  freely editable and avoids action-at-a-distance bugs.
- *Edit a template* — direct CRUD on a template, in a dedicated
  templates page (under Settings or a top-level *Mappings* nav).

**`.sdl` import/export** sits on top of the template model, not the
step's mapping:

- Import (`.sdl` → template): file becomes a new template the user can
  then apply to one or more steps. Preserves the imported file as a
  reusable artefact instead of a one-shot operation.
- Export (template → `.sdl`): write a template out for use in Data
  Loader or for sharing.

Format choice is still open — the original Option A/B tradeoff
applies, but is now a question about **the template's I/O format**,
not about a step-direct file load:

- **Option A (literal `.sdl`)** — drop-in compatibility with Data
  Loader. Lossy: can't carry `static_to_field`, `skip_column`,
  `ack_required`, or relationship-header niceties cleanly.
- **Option B (JSON-native, with `.sdl` import as a one-way path)** —
  clean long-term. Lets us round-trip the full template fidelity for
  templates created in this tool while still accepting `.sdl` from
  Data Loader users.
- **Option C (both)** — JSON for export and round-trip; `.sdl` accepted
  on import only. Probably the right answer; resolve in the epic.

**Why this consolidation is better.** Treating templates as the primary
and `.sdl` as an I/O format for templates means:

- A user importing a `.sdl` ends up with a **named, reusable
  template**, not a one-shot step mutation. They can apply it to ten
  steps, edit it once, etc.
- Permissions and ownership are clean — templates are a first-class
  scope, so we can RBAC them (org-wide vs. user-private templates).
- The migration story for users coming from Data Loader is "import
  your `.sdl` files into the templates library, then apply them to
  steps", which is more legible than "load this `.sdl` onto this
  step right now and lose the file".

**Forward compatibility from v1.** The `load_step_field_mapping` table
is intentionally a **parallel structure**, not a reference into
templates — steps own copies of their rows. This means v1 ships
without any template-related plumbing in the step's mapping table,
and W4-B can ship without a migration to the v1 schema.

---

## Resolved decisions log

The original open-questions list was worked through with the spec
author on 2026-05-10. Outcomes folded back into the locked decisions
above; recap here for traceability.

| # | Question | Resolution | Folded into |
|---|----------|-----------|-------------|
| Q1 | Default-on or opt-in for new DML steps? | **Opt-in** — new steps land with empty mapping; expanding the panel triggers describe + auto-map. | D1 ("New steps land empty"), D4 |
| Q2 | When to invalidate / refresh the describe cache? | **Fresh-on-open + manual refresh.** Auto-refetch on panel open if cache > 60 s; explicit Refresh button + "as of HH:MM" indicator. No re-fetch on save. Driven by the new-fields-just-appeared case (perms changed) being more common than fields disappearing. | D2 ("Freshness UX") |
| Q3 | Per-connection vs. per-org cache key? | **Per-connection.** Two connections to the same org may run as different users with different FLS, so a shared cache would leak invisible fields. Bounded redundancy is acceptable. | D2 |
| Q4 | Should query steps get a symmetric output-mapping panel? | **Out of scope.** Realistic use cases (cross-object transfer, field copy) are better served by Apex; receiving-side mapping covers in-plan chaining. Revisit if a generic step-transformation surface ever materialises. | Non-goals |
| Q5 | Delete steps: full grid or short-circuited combobox? | **Short-circuit** — single combobox picking the CSV column that holds `Id`. Persistence still writes one mapping row; only the editor UI differs. | D8 |
| Q6 | Polymorphic relationship pickers: flat list or two-step? | **Two-step** (parent SObject → external-ID field). Makes the load-time semantic choice explicit and reuses the same picker shape as single-target lookups. Single-target references skip the parent-pick step automatically. | D3 ("Polymorphic references") |

---

## Risks

- **Describe payloads are large.** `Account.describe()` on a mature org
  can exceed 200 KB. Cache aggressively and only forward the field subset
  the UI needs.
- **Org schema drift between edit time and run time.** The mapping was
  valid when saved but a field got deprecated or made non-writable.
  Mitigation: re-validate on preflight; the orchestrator's Bulk API
  failure mode is already loud (per-row error CSV with field-level
  messages), so the worst case is a noisy run, not corruption.
- **Mapping UI complexity creep.** The Data Loader's grid is dense; on
  100+ field SObjects it's overwhelming. Plan a virtualised list and a
  default filter to "fields that have a CSV column matched OR are
  required" to keep the initial view scannable.

---

## Related

- [Named step outputs](named-step-outputs.md) — the cross-step input model
  this builds on.
- [`docs/architecture.md`](../architecture.md) — orchestrator + CSV
  processor flow.
- Bulk API 2.0 docs — relationship headers for upsert / external-ID
  resolution.

---

## Changelog

- **2026-05-10** — Initial draft + Q&A pass. All eight design decisions
  (D1–D8) locked. Open questions Q1–Q6 resolved and folded back into the
  decision sections; resolution log retained for traceability.
- **2026-05-10** — Added *UI implementation guardrails* and
  *Documentation impact* sections after review. Wave-3 stories now
  carry explicit DoD bullets covering `ui-conventions.md` updates and
  the new `docs/usage/field-mapping.md` topic plus cross-link edits.
- **2026-05-10** — Technical review pass. Eight findings (4 High,
  4 Medium) folded back into the locked decisions:
    - **H1 Relationship header syntax was wrong.** Fixed in D3 — the
      original `{Relationship}.{ExternalIdField}:{LookupFieldApiName}`
      form is invalid Bulk API. Replaced with the documented
      non-polymorphic (`Relationship.IndexedField`) and polymorphic
      (`ParentObject:Relationship.IndexedField`) forms; example
      `Account.ExternalId__r:Account__c` removed.
    - **H2 Describe payload could not power lookup mappings.** D2 now
      documents lazy parent-object describe fetches as the source of
      external-ID-flagged fields, with the cache layer doing the heavy
      lifting on hit-rate. Optional `?expand=reference_targets` mode
      noted as a v2 escape hatch.
    - **H3 `defaulted_on_create` was missing from the response.** Added
      to D2's response shape and to the field-flag inventory table;
      D5 explicitly references the flag for the `insert`/`upsert`
      required-field rule. `id_lookup` added too — User.Username uses
      it instead of `external_id`.
    - **H4 Retry mapping was unsafe.** D6 now distinguishes Track A
      (Salesforce result-file replay — no mapping reapplication) from
      Track B (re-discover original source CSV — mapping flows through
      `partition_csv` naturally). `apply_mapping` is wired into
      `partition_csv` only. Regression test added to wave 1.
    - **M1 Skip / ack states were not persistable.** D1 redesigned
      around a `kind` enum with four decision types
      (`column_to_field`, `static_to_field`, `skip_column`,
      `ack_required`). CHECK constraint enforces per-kind nullability;
      partial unique indexes cover both source and destination sides.
    - **M2 Update operations needed an explicit Id rule.** D5 now has
      per-operation rules; `update` requires `Id` as a destination
      (Bulk API would silently fail otherwise — describe doesn't flag
      `Id` as required because it's nullable on most SObjects).
    - **M3 Source-side cardinality was ambiguous.** Added a partial
      unique index on `(load_step_id, csv_column)` and a callout in
      D1: 1:1 mapping is enforced for v1; broadcast-to-many is a v2
      concern.
    - **M4 Plan duplication didn't carry mappings.** New wave-1 story
      to extend `duplicate_plan` in `load_plan_service.py` to deep-copy
      mapping rows alongside the existing step copy + FK remap, with
      regression test.
- **2026-05-10** — Confirmed Bulk API 2.0 does **not** support delete
  by external ID — bulk deletion requests can include only the `Id`
  field. Note added under D8 along with implementer guardrails: the
  mapping panel must not offer relationship destinations on delete
  steps, the save validator must reject non-`Id` destinations on
  delete, and the Bulk client should defensively refuse
  `externalIdFieldName` when `operation == 'delete'` rather than
  letting Salesforce 400 the job. Source: Salesforce *Manage Records —
  Delete with Bulk API 2.0* docs.
- **2026-05-10** — Wave 4 restructured. The original three
  follow-up stories ("static-value UI", "`.sdl` import/export",
  "reusable mapping library") collapse into two epics: **W4-A
  Static-value mapping (UI)** and **W4-B Reusable mapping templates
  with `.sdl` interop**. Treating templates as the primary
  abstraction and `.sdl` as an I/O format on top is cleaner than
  bolting file load/save directly onto a step's mapping rows: a `.sdl`
  import becomes a named, reusable template the user can apply to
  many steps rather than a one-shot per-step mutation. New table
  schema sketch (`field_mapping_template` + `field_mapping_template_row`)
  added; deliberately a parallel structure to `load_step_field_mapping`
  so v1 ships without template plumbing.
- **2026-05-10** — Tickets created. Main epic SFBL-301; child stories
  SFBL-304..315; follow-up epics SFBL-302 (static value) and SFBL-303
  (templates + `.sdl`). Story-breakdown section updated with real keys.
- **2026-05-10** — Readiness-review fix pass. Five issues caught and
  resolved before implementation kickoff:
    1. **D5 upsert rule was too permissive.** Originally allowed the
       configured `external_id_field` to be satisfied by a
       relationship-header destination. That conflates two distinct
       concepts: `externalIdFieldName` in the Bulk API job payload
       names a field on the target SObject (the upsert key), while
       relationship headers resolve **parent lookups** to a related
       record by external ID. D5 now requires an exact destination
       match for `LoadStep.external_id_field` and explicitly rejects
       relationship-form satisfiers. Test case enumerated.
    2. **D6 partition_csv contract was implicit.** The function had
       no mapping argument and call sites in `step_executor.py` /
       Track B retry were undefined. D6 now specifies the new
       signature `partition_csv(..., mapping=None)`, makes loading
       and passing the rows the caller's responsibility, and notes
       which call sites need updating.
    3. **Header discovery was underspecified for non-literal patterns.**
       D6 now defines per-input-mode behaviour for auto-map header
       sourcing and preflight validation: literal local file (today),
       glob → first match for headers + every match validated at
       preflight, remote/connection same via storage adapter,
       `from_step` mode out of scope for v1 (mapping panel hidden
       with a deferred-feature note).
    4. **Stale `plans.edit` permission key.** The actual permission
       key in `backend/app/auth/permissions.py` is `plans.manage`.
       Replaced in the docs frontmatter and downstream tickets;
       prevents `docs-drift` CI failure and RBAC drift.
    5. **Background section example used the bad relationship form.**
       Replaced with the documented forms (`Account__r.External_Id__c`
       and `User:Owner.Username`).
