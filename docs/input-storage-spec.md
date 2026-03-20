# Spec: Input Connections and Cloud Storage for the Salesforce Bulk Loader

## Overview

The app currently supports input files only from the local filesystem under the configured input directory. This spec covers expanding the system so input files can come from multiple sources:

- **Stage 1**: introduce first-class input connections and source-aware browsing for local files plus Amazon S3
- **Stage 2**: make load-step preview and run execution source-aware so plans can execute from local or remote input sources
- **Stage 3**: generalize the provider model for future storage backends such as Azure Blob Storage or Google Cloud Storage

This document follows the same staged implementation model as the auth spec so the work can be delivered incrementally without losing architectural coherence.

---

## Phase 0 Decisions

The following decisions are fixed unless explicitly changed in a later revision:

| Topic | Decision |
|---|---|
| Salesforce vs input credentials | Keep as **separate connection types** |
| Existing `connection` table | Remains **Salesforce-only** |
| New input-source model | Add distinct **`input_connection`** entity |
| Initial cloud provider | **Amazon S3** |
| Local files support | Remains supported as a **built-in source** |
| Input source selection | Files UI and step editor must support **local and remote** sources |
| Step-level source binding | Store source selection on the **load step** |
| File pattern storage | Continue storing **relative patterns**, not full S3 URIs |
| Local source representation | Use a built-in **`local`** source, not a DB row |
| Secrets handling | Encrypt remote secrets at rest, same pattern as Salesforce private keys |
| Source browsing API | Extend current files API to be **source-aware** rather than creating an unrelated browser API |
| Row counts in listings | May be **nullable** for remote sources if expensive |
| S3 folder semantics | Treat prefixes as **virtual directories** in the UI |
| Future providers | Design for extension, but implement only **S3** initially |

---

## Current State

| Layer | Status |
|---|---|
| Input file browsing API | Local filesystem only |
| Input file preview API | Local filesystem only |
| Step preview | Resolves glob under `settings.input_dir` only |
| Run execution | Discovers and partitions local files only |
| Connections UI | Salesforce connections only |
| Files UI | Assumes a single unnamed local input directory |
| Step editor file picker | Assumes local input directory only |

The current implementation is tightly coupled to `settings.input_dir` in the backend and to a single implicit local source in the frontend.

---

## Architecture

### Connection Domains

The system should model two separate connection domains:

1. **Salesforce connections**
   - target system credentials used for Bulk API operations
2. **Input connections**
   - source-system or storage credentials used to browse and read input files

These should remain distinct in both backend and frontend models. They have different required fields, validation rules, test behavior, and execution paths.

### Input Source Model

Input sources should be represented as:

- built-in `local` source
- persisted `input_connection` records for remote providers

This gives a stable UX:

- users can always browse local files
- remote sources appear alongside local in selectors
- steps can bind to one input source without overloading the Salesforce connection model

### Storage Abstraction

Add a storage abstraction between file consumers and the underlying provider.

Suggested interface:

- `list_entries(path: str) -> list[InputEntry]`
- `preview_file(path: str, rows: int) -> InputPreview`
- `discover_files(pattern: str) -> list[InputObject]`
- `open_text(path: str)` or `open_binary(path: str)`

Note: `iter_csv_partitions` / CSV partitioning is **not** part of the storage interface.
Partitioning stays in `csv_processor.partition_csv`, which will accept provider-neutral
file handles after Ticket 9.  The storage interface provides `open_text`/`open_binary`
so that `csv_processor` can remain provider-neutral without exposing CSV logic through
the storage layer.

Implementations:

- `LocalInputStorage`
- `S3InputStorage`

Resolver:

- `source=local` resolves `LocalInputStorage`
- `source=<input_connection_id>` resolves remote storage from `input_connection`

### Step Binding

Each `load_step` should be able to declare which input source its file pattern belongs to.

Recommended schema change:

- add nullable `input_connection_id` to `load_step`

Semantics:

- `input_connection_id = null` means local source
- non-null means resolve pattern within that input connection's root

Keep `csv_file_pattern` as a source-relative path or glob. Do not store full `s3://bucket/prefix/file.csv` URIs in steps.

### UI Model

The UI should treat input sources as first-class selectable contexts:

- `Connections` page: separate Salesforce and input connection sections
- `Files` page: source selector plus source-aware browser
- `Plan Editor`: per-step input source selector plus source-aware file picker

The browser experience should stay consistent across providers even if some metadata is unavailable remotely.

---

## Stage 1: Input Connections and Source-Aware Browsing

### Backend Design

#### Config additions (`backend/app/config.py`)

No provider-specific config is required for stored S3 connections beyond existing encryption support. The existing encryption mechanism should be reused for sensitive input-connection fields.

Optional future config:

```python
input_preview_row_scan_limit: int = 100000
```

This is optional and only becomes useful if remote row counting needs explicit guardrails.

#### Input connection model (`backend/app/models/input_connection.py`)

Add a new SQLAlchemy model:

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `name` | string | User-facing display name |
| `provider` | enum/string | Initially `s3` |
| `bucket` | string | Required for S3 |
| `root_prefix` | string nullable | Logical root within bucket |
| `region` | string nullable | Optional for S3 client config |
| `access_key_id` | text | Sensitive but not necessarily encrypted if policy allows; encryption still preferred |
| `secret_access_key` | text | Encrypted at rest |
| `session_token` | text nullable | Encrypted at rest |
| `created_at` | datetime | |
| `updated_at` | datetime | |

Notes:

- all sensitive S3 credentials should be treated as secrets
- root prefix should be normalized so browsing and matching use one consistent base

#### Migration

Add a new Alembic migration for:

- `input_connection` table
- `load_step.input_connection_id` nullable FK to `input_connection.id`
- supporting index on `load_step.input_connection_id`

Delete behavior:

- use `RESTRICT` for `load_step -> input_connection`
- do not allow deleting an input connection if steps still reference it

#### Schemas

Add new input connection schemas:

- `InputConnectionCreate`
- `InputConnectionUpdate`
- `InputConnectionResponse`
- `InputConnectionTestResponse`

Secrets must be omitted from response models.

Update load-step schemas:

- include `input_connection_id: Optional[str]`

#### Input connections router

Add new routes:

| Endpoint | Description |
|---|---|
| `GET /api/input-connections/` | List input connections |
| `POST /api/input-connections/` | Create input connection |
| `GET /api/input-connections/{id}` | Fetch one input connection |
| `PUT /api/input-connections/{id}` | Update input connection |
| `DELETE /api/input-connections/{id}` | Delete input connection |
| `POST /api/input-connections/{id}/test` | Validate remote access |

S3 test behavior should verify:

- credentials can authenticate
- bucket is accessible
- configured root prefix is readable

`POST /test` should follow the existing Salesforce pattern:

- return a body with `success: true/false`
- include a human-readable message

#### Files API changes (`backend/app/api/utility.py`)

Extend the current files API to accept a source selector.

List:

```txt
GET /api/files/input?source=local&path=subdir
GET /api/files/input?source=<input_connection_id>&path=folder
```

Preview:

```txt
GET /api/files/input/{file_path}/preview?source=local&rows=25
GET /api/files/input/{file_path}/preview?source=<input_connection_id>&rows=25
```

Suggested response shape for directory entries:

```json
{
  "name": "accounts.csv",
  "kind": "file",
  "path": "accounts.csv",
  "size_bytes": 2048,
  "row_count": 100,
  "source": "local",
  "provider": "local"
}
```

Notes:

- `row_count` may be `null` for remote objects if counting would require a full scan
- `provider` should be explicit for frontend clarity
- source-aware errors should distinguish invalid path from missing connection

#### Storage service

Add a new service module to own provider resolution and provider-specific logic.

Recommended responsibilities:

- resolve input source identifier to a provider
- normalize root paths and prefixes
- translate provider-specific results into shared DTOs
- centralize path-safety validation

Local provider responsibilities:

- preserve current path traversal protections
- continue hiding dotfiles/directories if that remains desired behavior

S3 provider responsibilities:

- list prefixes and `.csv` objects
- emulate directories from prefixes
- preview CSV contents from object reads
- support file discovery by glob-like matching relative to root prefix

### Frontend Design

#### API types (`frontend/src/api/types.ts`)

Add:

- `InputConnection`
- `InputConnectionCreate`
- `InputConnectionTestResponse`
- input source metadata on file entry / preview types

Recommended source selector model:

```ts
type InputSourceOption =
  | { id: 'local'; kind: 'local'; name: 'Local Files'; provider: 'local' }
  | { id: string; kind: 'connection'; name: string; provider: 's3' }
```

#### API endpoints (`frontend/src/api/endpoints.ts`)

Add:

- `inputConnectionsApi`

Update:

- `filesApi.listInput(source, path)`
- `filesApi.previewInput(source, filePath, rows)`

#### Connections page (`frontend/src/pages/Connections.tsx`)

Expand the page to include both connection domains.

Recommended UI structure:

- section or tabs for `Salesforce Connections`
- section or tabs for `Input Connections`

Do not attempt to unify both forms into one generic "connection" dialog. That would make validation and UX worse immediately.

S3 form fields:

- Name
- Bucket
- Root Prefix
- Region
- Access Key ID
- Secret Access Key
- Session Token optional

#### Files page (`frontend/src/pages/FilesPage.tsx`)

Add a source selector above the breadcrumb.

Behavior:

- default to `Local Files`
- list remote input connections as additional options
- reset breadcrumb and selection when source changes
- all browse and preview queries include source

Copy changes:

- replace references to "the input directory"
- use source-aware wording such as "selected input source"

#### Plan editor (`frontend/src/pages/PlanEditor.tsx`)

Add `Input Source` to the step modal.

Behavior:

- default to local for new steps
- preselect the saved source for existing steps
- file picker queries the selected source
- literal file header preview for upsert uses the selected source

Notes:

- source selection belongs to the step, not only to the plan
- different steps in a plan may intentionally read from different input sources

### Testing

Backend additions:

- tests for input connection CRUD and test endpoint
- tests for source-aware file listing and preview
- tests for load-step create/update carrying `input_connection_id`

Frontend additions:

- Connections page tests for the new input-connection section
- Files page tests for source selection and source-aware browsing
- Plan editor tests for source-aware file picker behavior

Regression expectation:

- existing file-browser tests will need updated API signatures
- step tests will need `input_connection_id` coverage

---

## Stage 2: Source-Aware Step Preview and Run Execution

Stage 1 is not sufficient by itself. Browsing remote files without enabling execution would create a misleading UX. Stage 2 closes that gap.

### Backend Design

#### Load-step preview (`backend/app/api/load_steps.py`)

Current behavior resolves:

- `settings.input_dir + step.csv_file_pattern`

New behavior should:

1. inspect `step.input_connection_id`
2. resolve the appropriate storage provider
3. discover matching files relative to that source root
4. count preview rows provider-neutrally

The endpoint contract can remain unchanged if the source is fully derived from the step.

#### CSV processing (`backend/app/services/csv_processor.py`)

Current behavior is local-path based.

Refactor intent:

- keep CSV parsing, encoding detection, header validation, and partition rendering generic
- separate provider-specific discovery and opening from CSV logic

Recommended split:

- provider-neutral CSV utilities continue to operate on file-like streams or bytes
- discovery moves behind the storage abstraction

#### Orchestrator (`backend/app/services/orchestrator.py`)

Current behavior calls local `discover_files(step.csv_file_pattern)` and `partition_csv(path, size)`.

New behavior should:

1. resolve step input source
2. discover matching files through the storage abstraction
3. stream or download content in a provider-neutral way
4. partition CSV content without assuming a local path

Recommended implementation direction:

- do not require full remote files to be downloaded to durable disk
- allow temporary local buffering only as an implementation detail if necessary
- keep per-partition memory bounded as it is today

### Execution Semantics

For a step with `input_connection_id = null`:

- behavior should be unchanged from the current local execution path

For a step with `input_connection_id` set:

- `csv_file_pattern` is evaluated relative to the connection's bucket + root prefix
- matched objects are processed in sorted order
- partitioning semantics remain identical to local files

### Error Handling

Expected new failure categories:

- invalid or deleted input connection referenced by a step
- authentication failure against S3
- inaccessible bucket or prefix
- remote object disappears between preview and execution
- unsupported remote file encoding or malformed CSV

Recommended run behavior:

- treat source access failures as step failures
- include clear source-related detail in `error_summary` and job-level errors

### Testing

Backend additions:

- unit tests for storage-provider resolution
- load-step preview tests for local and remote sources
- orchestrator tests for remote discovery and partition flow

Notes:

- remote-provider tests should rely on mocks/fakes rather than live AWS dependencies

---

## Stage 3: Provider Generalization

After S3 is stable, the design should allow new providers without restructuring the app.

### Design Rules

- frontend types should use discriminated unions for provider-specific config
- backend provider resolution should be interface-based, not `if provider == ...` spread across route handlers
- file entry and preview contracts should stay provider-neutral

### Candidate Future Providers

- Azure Blob Storage
- Google Cloud Storage
- SFTP or managed file shares

This spec does not include implementation details for those providers, only the architectural requirement that S3 should not be hardcoded into the entire app.

---

## Security Considerations

| Concern | Mitigation |
|---|---|
| Cloud credentials leaked from API responses | Never return secret fields |
| Cloud credentials leaked at rest | Encrypt secret-bearing fields |
| Overloaded connection model causing secret mix-ups | Keep Salesforce and input connections separate |
| Path traversal on local source | Preserve existing local path validation |
| Over-broad S3 access | Recommend bucket/prefix scoping and least-privilege IAM |
| Large remote preview cost | Allow nullable `row_count` and limit preview rows |
| Input connection deleted while referenced | Use FK restrict behavior |
| Source mismatch between picker and execution | Persist source on each load step |

---

## Environment Variables

No new environment variables are required for stored S3 connections if credentials are persisted in the database and encrypted using the existing encryption key.

Existing required variable reused:

| Variable | Required | Description |
|---|---|---|
| `ENCRYPTION_KEY` | Yes | Used to encrypt remote secret fields at rest |

Optional future tuning variables:

| Variable | Required | Description |
|---|---|---|
| `INPUT_PREVIEW_ROW_SCAN_LIMIT` | No | Guardrail for expensive row counts |

---

## Implementation Tickets

These tickets are intended to be small enough to execute incrementally while still producing coherent checkpoints. They are ordered and dependency-aware so they can be handed off one at a time.

### 1. Add Backend Input Connection Model and Migration

Goal: create the persistence foundation for remote input sources.

Scope:

- add `InputConnection` SQLAlchemy model
- export the model through the backend model package
- add Alembic migration for `input_connection`
- add nullable `load_step.input_connection_id`
- add supporting FK/indexes

Notes:

- keep the existing Salesforce `connection` model unchanged
- `input_connection_id = null` should remain valid for local files

Dependencies:

- none

Exit criteria:

- database migrates successfully
- application imports cleanly with the new model

### 2. Add Backend Input Connection Schemas and CRUD/Test API

Goal: expose remote input connections as a first-class backend resource.

Scope:

- add Pydantic schemas for input connections
- add `backend/app/api/input_connections.py`
- implement CRUD routes
- implement `POST /api/input-connections/{id}/test`
- register router in `backend/app/main.py`
- add backend API tests

Notes:

- redact all secret fields in responses
- follow the same `success/message` test-response pattern used by Salesforce connections

Dependencies:

- Ticket 1

Exit criteria:

- input connections can be created, listed, updated, deleted, and tested

### 3. Add Backend Input Storage Abstraction

Goal: remove direct provider assumptions from file-browsing consumers.

Scope:

- ~~add a provider-neutral storage service module~~ **done in Phase 1.2 refactoring** (`backend/app/services/input_storage.py`)
- ~~implement local provider adapter~~ **done in Phase 1.2 refactoring** (`LocalInputStorage`)
- implement S3 provider adapter (`S3InputStorage`)
- add `get_storage(source: str, db) -> BaseInputStorage` resolver for `local` and DB-backed sources
- add unit tests for S3 provider and resolver

Notes:

- `LocalInputStorage`, shared DTOs (`InputEntry`, `InputPreview`), and `detect_encoding` already exist
- the abstract base class / Protocol can be formalised in this ticket if not done in Phase 1.2
- keep the abstraction small and oriented around actual app use cases

Dependencies:

- Ticket 2 (resolver needs DB-backed input connections; local provider has no dependency)

Exit criteria:

- storage consumers can request list/preview/discover behavior without branching on provider details

### 4. Make Files API Source-Aware

Goal: support browsing and previewing local and remote sources through the existing files API.

Scope:

- ~~route both endpoints through the storage abstraction~~ **done in Phase 1.2 refactoring** (both endpoints already delegate to `LocalInputStorage`)
- add `source` query parameter to `/api/files/input` and the preview endpoint
- add resolver dispatch: `source=local` → `LocalInputStorage`, `source=<id>` → `S3InputStorage`
- update backend tests for source-aware list/preview behavior

Notes:

- preserve local behavior when `source=local` or `source` is omitted
- the routing-through-abstraction work is complete; this ticket adds the source-selector

Dependencies:

- Ticket 3

Exit criteria:

- local and S3 sources can both be browsed and previewed through the files API

### 5. Expand Frontend Connections Page for Input Connections

Goal: surface remote input sources in the UI without disturbing Salesforce connection behavior.

Scope:

- add frontend types and endpoints for input connections
- update `Connections` page to show Salesforce and input sections
- add create/edit/delete/test flow for S3 connections
- add/update page tests

Notes:

- do not replace the Salesforce form with a generic polymorphic form unless that becomes necessary later

Dependencies:

- Ticket 2

Exit criteria:

- users can manage S3 input connections from the browser

### 6. Make Files Page Source-Aware

Goal: let users browse both local and remote file stores from the Input Files page.

Scope:

- add input source selector
- fetch input connections alongside local source option
- reset path and file selection on source change
- update list and preview calls to include source
- update copy and empty states to be source-aware
- add/update page tests

Notes:

- preserve current local UX as the default state

Dependencies:

- Tickets 4 and 5

Exit criteria:

- Files page supports browsing local files and S3-backed sources

### 7. Add Step-Level Input Source Selection

Goal: bind each load step to the source its file pattern should resolve against.

Scope:

- extend load-step schemas and frontend types with `input_connection_id`
- update step create/update endpoints to accept the field
- update step modal UI with `Input Source`
- update file picker in `PlanEditor` to browse the selected source
- update tests for step create/edit flows

Notes:

- default to local for new steps
- existing steps should continue to behave as local

Dependencies:

- Tickets 1, 4, and 5

Exit criteria:

- each step can select local or a configured remote input connection

### 8. Make Step Preview Source-Aware

Goal: ensure preflight preview reflects the step's actual configured input source.

Scope:

- update load-step preview endpoint to resolve source from `input_connection_id`
- update file matching logic to use the storage abstraction
- add backend tests for local and remote preview behavior

Notes:

- endpoint contract can remain unchanged if the source is derived from the step

Dependencies:

- Tickets 3 and 7

Exit criteria:

- step preview returns correct matched files and row counts for local and S3-backed steps

### 9. Refactor CSV Processing Around Provider-Neutral Inputs

Goal: decouple CSV parsing and partitioning from local filesystem paths.

Scope:

- ~~split provider-specific discovery/opening from CSV logic~~ **done in Phase 1.2 refactoring** (`csv_processor.discover_files` is now a thin wrapper over `LocalInputStorage`)
- adapt `partition_csv` to accept provider-neutral input handles or streams (rather than a local `pathlib.Path`)
- preserve current encoding normalization and partition semantics
- add/update unit tests

Notes:

- file discovery from `csv_processor` is already redirected to the storage abstraction
- remaining work: make `partition_csv` accept an open file handle or stream so that remote providers can supply content without a local path
- hardening requirement: remote storage readers must not require loading an entire object into memory before CSV processing begins; S3-backed `open_text` / partitioning flow should support streaming reads so large remote files preserve the same bounded-memory behavior as local files
- this ticket is internal refactoring but required before remote execution can be reliable

Dependencies:

- Ticket 3

Exit criteria:

- CSV processing no longer assumes every input is a local path

### 10. Make Orchestrator Execution Source-Aware

Goal: allow actual load runs to execute from remote input sources.

Scope:

- update step execution flow to resolve local vs remote input source
- discover files via storage abstraction
- partition and process matched files provider-neutrally
- add backend tests for remote execution flow using mocks/fakes

Notes:

- keep memory behavior bounded
- avoid introducing a hard dependency on persistent local temp files unless necessary

Dependencies:

- Tickets 7, 8, and 9

Exit criteria:

- runs can execute end-to-end using either local files or S3-backed input sources

### 11. Stage 1 and Stage 2 Regression Pass

Goal: stabilize the combined browsing plus execution feature set.

Scope:

- review API and UI naming consistency around source vs connection
- clean up duplicated provider branching
- review test coverage around secret redaction and source selection
- verify remote CSV execution paths preserve bounded-memory streaming behavior and do not buffer whole S3 objects in memory
- run regression coverage for files page, plan editor, step preview, and run execution

Dependencies:

- Tickets 1 through 10

Exit criteria:

- local workflows remain intact
- remote workflows are documented, tested, and operational

### 12. Provider Generalization Follow-Up

Goal: prepare the S3 implementation so future providers can be added cleanly.

Scope:

- review provider abstractions for S3 leakage into shared contracts
- convert frontend and backend models to discriminated/provider-based patterns where needed
- document extension points for future providers

Notes:

- this is a design-hardening ticket, not a commitment to add another provider immediately

Dependencies:

- Ticket 11

Exit criteria:

- the app is structurally ready for at least one additional storage provider without major redesign

---

## Suggested Work Split

### Agent A: Backend

- input connection model and migration
- input connection API
- storage abstraction
- files API source support
- step preview changes
- CSV processing refactor
- orchestrator execution changes
- backend tests

### Agent B: Frontend

- input connection types and endpoints
- Connections page expansion
- Files page source selector
- Plan editor source selection and file picker
- frontend tests

Recommended coordination checkpoints:

1. After input connection backend contract completion
2. After source-aware files API completion
3. After frontend source-selection rollout
4. After orchestrator remote execution delivery

---

## Files Likely Affected

### Backend

| File | New / Modified |
|---|---|
| `backend/app/models/input_connection.py` | New |
| `backend/app/models/load_step.py` | Modified |
| `backend/app/models/__init__.py` | Modified |
| `backend/app/schemas/input_connection.py` | New |
| `backend/app/schemas/load_step.py` | Modified |
| `backend/app/api/input_connections.py` | New |
| `backend/app/api/utility.py` | Modified |
| `backend/app/api/load_steps.py` | Modified |
| `backend/app/services/input_storage.py` | New |
| `backend/app/services/csv_processor.py` | Modified |
| `backend/app/services/orchestrator.py` | Modified |
| `backend/app/main.py` | Modified |
| `backend/alembic/versions/<new_revision>.py` | New |

### Frontend

| File | New / Modified |
|---|---|
| `frontend/src/api/types.ts` | Modified |
| `frontend/src/api/endpoints.ts` | Modified |
| `frontend/src/pages/Connections.tsx` | Modified |
| `frontend/src/pages/FilesPage.tsx` | Modified |
| `frontend/src/pages/PlanEditor.tsx` | Modified |

### Tests

| File | New / Modified |
|---|---|
| `backend/tests/test_input_connections.py` | New |
| `backend/tests/test_utility.py` | Modified |
| `backend/tests/test_load_steps.py` | Modified |
| `backend/tests/test_csv_processor.py` | Modified |
| `backend/tests/test_orchestrator.py` | Modified |
| `frontend/src/__tests__/pages/Connections.test.tsx` | Modified |
| `frontend/src/__tests__/pages/FilesPage.test.tsx` | Modified |
| `frontend/src/__tests__/pages/PlanEditor.test.tsx` | Modified |
| `frontend/src/__tests__/api/endpoints.test.ts` | Modified |
