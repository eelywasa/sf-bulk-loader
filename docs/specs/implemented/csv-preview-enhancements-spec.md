# Spec: CSV Preview Pagination, Search, and Filtering

**Jira Epic: SFBL-14**

## Overview

The app currently shows a fixed 25-row preview whenever a CSV file is displayed. This spec
covers adding server-side pagination and submit-based column filtering to every CSV preview
context in the application.

There are two distinct preview contexts:

- **Input file preview** (`FilesPage`) — browsing local or S3-backed CSV files before a run
- **Job result CSV preview** (`JobDetail`) — reviewing success, error, and unprocessed records
  after a Bulk API run

Both will share the same frontend component and follow the same UX pattern, so users encounter
a consistent experience regardless of which CSV they are looking at.

---

## Phase 0 Decisions

| Topic | Decision |
|---|---|
| Pagination strategy | **Server-side** — offset + limit parameters on each API request |
| Filter trigger | **Submit-based** — user specifies filter criteria and presses Apply; no live per-keypress scanning |
| Filter match semantics | **Case-insensitive substring** (contains) match on each specified column |
| Multi-column filtering | Multiple filters are **ANDed** together |
| Filter parameter encoding | JSON array string `?filters=[{"column":"Col","value":"val"}]` |
| Filter validation | Unknown columns, blank column names, malformed filter objects, and duplicate columns return **HTTP 400** |
| Page size options | 25, 50, 100, 250 — default **50** |
| Maximum page size | 500 rows per request (caps server load) |
| Backward compatibility | New parameters are optional; existing callers (e.g. PlanEditor header fetch) continue to work unchanged |
| Pagination metadata | Responses include `has_next` so the UI can paginate even when total counts are unknown |
| S3 memory model | Use `open_text()` streaming for all S3 preview reads; retain only the current page window in memory |
| Total row count — unfiltered | `total_rows`: **null by default**; V1 does not perform a second full scan just to compute exact totals |
| Total row count — filtered | `filtered_rows`: exact and always computed on submit via full-file parsed scan; `total_rows` may also be populated from the same scan |
| Job result CSV files | Always local; follow the same parsed-preview semantics as input previews rather than newline-count approximations |
| `row_count` field (existing) | Retained as a **deprecated compatibility field** during migration; continues to mean "rows returned in this page" and is removed only in a later cleanup ticket |
| Shared component location | `frontend/src/components/ui/CsvPreviewPanel.tsx` |

---

## Current State

| Layer | Status |
|---|---|
| Input file preview API (`/api/files/input/{path}/preview`) | Fixed `rows` parameter, no offset, no filtering |
| Job result CSV preview APIs | Fixed `rows` parameter, no offset, no filtering |
| `BaseInputStorage.preview_file()` | Single `rows: int` parameter; returns rows + count |
| `InputPreview` DTO | `filename`, `header`, `rows`, `row_count` (count of returned rows only) |
| `FilesPage` preview panel | Shows first 25 rows, no pagination, no filtering |
| `JobDetail` log sections | Shows first 25 rows with "Showing first 25 rows" footer, no pagination, no filtering |
| Shared CSV preview component | None — both contexts have their own inline table implementations |

---

## Architecture

### Guiding Principles

- Pagination and filtering are server-side concerns. The backend scans the file; the frontend
  only requests pages.
- Filtering is submit-triggered, not live. This avoids per-keypress file scans on potentially
  large inputs.
- A single shared `CsvPreviewPanel` component handles both preview contexts. The component is
  decoupled from the API layer via a `fetchPage` prop — callers supply the function that fetches
  data, and the component manages state.

### Contexts in Scope

| Context | File source | API endpoint |
|---|---|---|
| FilesPage input preview | Local or S3 via `BaseInputStorage` | `GET /api/files/input/{path}/preview` |
| JobDetail success CSV | Local output directory | `GET /api/jobs/{job_id}/success-csv/preview` |
| JobDetail error CSV | Local output directory | `GET /api/jobs/{job_id}/error-csv/preview` |
| JobDetail unprocessed CSV | Local output directory | `GET /api/jobs/{job_id}/unprocessed-csv/preview` |

Out of scope:

- PlanEditor file picker — calls `previewInput` with `rows=1` only to obtain column headers. No
  pagination or filter UI is needed here.
- Step preview — shows matched file names and row counts, not CSV row content.

---

## API Design

### Extended `InputPreview` DTO

The `InputPreview` dataclass in `backend/app/services/input_storage.py` gains pagination and
filter metadata while retaining the existing `row_count` field during migration:

```python
@dataclass
class InputPreview:
    filename: str
    header: list[str]
    rows: list[dict]            # rows for the current page
    total_rows: int | None      # exact total rows when known without an extra scan
    filtered_rows: int | None   # exact total rows matching active filters; null when no filters applied
    offset: int                 # row offset of this page (0-based, excludes header)
    limit: int                  # page size requested
    has_next: bool              # whether another page exists after this one
    row_count: int              # deprecated; retained during migration, always len(rows)
```

### Input file preview endpoint

`GET /api/files/input/{file_path:path}/preview`

New query parameters (all optional):

| Parameter | Type | Default | Constraint | Notes |
|---|---|---|---|---|
| `limit` | int | 50 | 1–500 | Replaces `rows`; `rows` retained as a deprecated alias |
| `offset` | int | 0 | ≥ 0 | 0-based row offset, header not counted |
| `filters` | string | — | valid JSON array | `[{"column":"ColumnName","value":"value"}, ...]` |

Response schema (`InputPreviewResponse`) gains the new fields from `InputPreview`, while retaining
the existing deprecated `row_count` field during migration.

Backward compatibility:
- `rows` query parameter continues to work as an alias for `limit`
- When `rows` is supplied without `offset`, behaviour is identical to the current API
- `row_count` remains present until all frontend consumers have migrated off it

### Job result CSV preview endpoints

All three endpoints (`/api/jobs/{job_id}/success-csv/preview`, `error-csv/preview`,
`unprocessed-csv/preview`) gain the same three parameters with the same semantics:

| Parameter | Type | Default | Constraint |
|---|---|---|---|
| `limit` | int | 50 | 1–500 |
| `offset` | int | 0 | ≥ 0 |
| `filters` | string | — | valid JSON array |

Response shape gains `total_rows`, `filtered_rows`, `offset`, `limit` alongside the existing
`filename`, `header`, `rows`, deprecated `row_count`, and new `has_next`.

---

## Backend Processing Semantics

### Unfiltered reads

1. Open file (local: `open()`; S3: `open_text()` stream)
2. Advance `csv.DictReader` past `offset` rows
3. Read the next `limit + 1` rows
4. Return the first `limit` rows as the page and derive `has_next` from the sentinel row
5. Return `total_rows = None` unless an exact count is already available from trusted metadata
   without an extra scan
6. `filtered_rows` is `None`

Note: reaching a given offset requires reading through preceding rows sequentially — CSV files
do not support random access. This is O(offset + limit) and acceptable for V1.

### Filtered reads

1. Open file and scan all rows
2. Apply each filter as a case-insensitive substring match on the named column
3. Count all matching rows, but retain only rows in the requested page window
4. Derive `has_next` from whether more than `offset + limit` matches exist
5. Return:
   - `rows` — the requested page only
   - `filtered_rows` — exact total matching row count
   - `total_rows` — exact total parsed row count when the full scan already made it available
   - `has_next` — whether another filtered page exists

Full-file scan is required to produce an accurate `filtered_rows` count. Because filtering is
submit-based (not live), this cost is incurred only when the user explicitly requests it. The
implementation must remain bounded-memory: it must not collect the full match set before slicing.

### Job result CSV totals

Job result files are always local, but V1 should not use raw newline counting for pagination
metadata because quoted newlines make those counts inaccurate. Job-result previews follow the same
rules as input previews:

- Unfiltered reads: use `has_next`; `total_rows` is optional and usually `null`
- Filtered reads: `filtered_rows` is exact; `total_rows` may be populated from the same parsed scan

### Threading model

CSV preview scanning remains synchronous Python code. Preview endpoints must therefore offload the
storage/file read work to a worker thread (for example via `fastapi.concurrency.run_in_threadpool`)
so large local or S3 scans do not block the event loop.

---

## Frontend Component Design

### `CsvPreviewPanel`

Location: `frontend/src/components/ui/CsvPreviewPanel.tsx`

#### Props

```ts
interface FilterRule {
  column: string
  value: string
}

interface CsvFetchParams {
  offset: number
  limit: number
  filters: FilterRule[]
}

interface CsvPageResult {
  filename?: string
  header: string[]
  rows: Record<string, string | null>[]
  total_rows: number | null
  filtered_rows: number | null
  offset: number
  limit: number
  has_next: boolean
}

interface CsvPreviewPanelProps {
  /** Base React Query key — component appends pagination/filter state */
  queryKey: unknown[]
  /** Called whenever the component needs a page of data */
  fetchPage: (params: CsvFetchParams) => Promise<CsvPageResult>
  /** Optional display label shown above the table */
  filename?: string
}
```

#### Internal state

| State | Type | Notes |
|---|---|---|
| `page` | number | 1-indexed current page |
| `pageSize` | number | Rows per page; options 25/50/100/250; default 50 |
| `draftFilters` | `FilterEntry[]` | Uncommitted filter rows in the UI |
| `activeFilters` | `FilterEntry[]` | Filters submitted to the backend |

`draftFilters` changes as the user edits the filter form. `activeFilters` only changes when the
user presses Apply. React Query is keyed on `activeFilters` and `page`/`pageSize`, so a query is
only issued on those changes.

When a new file is loaded (the base `queryKey` changes), the component resets `page`, `draftFilters`,
and `activeFilters` to their defaults.

#### Filter bar UX

- Displayed above the table
- Shows zero or more active filter rows: `[Column ▼] [contains] [value         ] [×]`
- "Add Filter" button appends a new blank filter row (column selector populated from `header`)
- Duplicate column selection is not allowed in the UI
- "Apply" button commits `draftFilters` → `activeFilters` and resets to page 1
- "Clear Filters" removes all filters and resets to page 1
- When no filters are active, the filter bar shows only the "Add Filter" button
- Apply is disabled while any filter row is incomplete or duplicates another selected column

#### Pagination controls

Displayed below the table:

```
[ ← First ] [ ‹ Prev ]   Page 3 of 12  (150 rows)   [ Next › ] [ Last → ]
                             [Page size: 50 ▼]
```

- "Page X of Y" is calculated from `total_rows` (unfiltered) or `filtered_rows` (when filters are
  active). If the total is unknown, show "Page X" only.
- "N rows" shows `filtered_rows` when filters are active, `total_rows` otherwise, omitted if `null`.
- First / Last buttons are disabled when already on the first or last page.
- When the total is unknown, hide or disable "Last".
- "Next" is controlled by `has_next`, not inferred from totals.

#### Loading and error states

- Loading: spinner overlay on the table (not a full-page spinner, so the filter bar remains
  interactive)
- Error: inline error message below the filter bar
- Empty (no rows returned): `EmptyState` component with message "No rows match the current filters"
  (if filters active) or "This file contains no data rows" (if no filters)

---

## Implementation Tickets

Maintenance note: when a ticket is completed, append `— ✅ DONE` to its heading and update this
section to reflect the implementation state.

### Ticket 1 — Extend Storage Abstraction for Pagination and Filtering — ✅ DONE (SFBL-61)

Goal: update the provider-neutral contract and both storage implementations to support offset,
limit, and column filters.

Scope:

- Update `InputPreview` dataclass: add `total_rows`, `filtered_rows`, `offset`, `limit`,
  `has_next`; retain deprecated `row_count`
- Update `BaseInputStorage` Protocol: new `preview_file` signature
  `(path: str, limit: int, offset: int, filters: list[dict[str, str]] | None) -> InputPreview`
- Update `LocalInputStorage.preview_file()`:
  - Unfiltered: read to offset, yield `limit + 1` rows, set `has_next` from the sentinel row,
    `total_rows = None`, `filtered_rows = None`
  - Filtered: full parsed scan, validate requested columns against the header, count exact
    `filtered_rows`, retain only the requested page rows, and populate `total_rows` from the same
    scan
- Update `S3InputStorage.preview_file()`:
  - Switch from `_get_object_bytes()` to `open_text()` for all paths (streaming)
  - Unfiltered: stream to offset, yield `limit + 1` rows; `total_rows = None`
  - Filtered: full scan via stream, validate requested columns, count exact `filtered_rows`,
    retain only the requested page rows, and optionally populate `total_rows` from the same scan
- Add unit tests for `LocalInputStorage` covering: first page, middle page, last page, filters
  that match some rows, filters with no matches, duplicate/unknown filter columns, `has_next`
- Add unit tests for `S3InputStorage` (mock boto3) covering the same scenarios

Dependencies: none

Exit criteria:
- Both providers handle pagination and filtering correctly in tests
- `S3InputStorage` no longer reads full objects or full match sets into memory for preview operations

---

### Ticket 2 — Extend Input File Preview API Endpoint — ✅ DONE (SFBL-63)

Goal: expose pagination and filtering through the files API.

Scope:

- Add `limit: int`, `offset: int`, `filters: Optional[str]` query parameters to
  `GET /api/files/input/{file_path:path}/preview` in `backend/app/api/utility.py`
- Keep `rows` as a deprecated alias for `limit` (if both are supplied, `limit` takes precedence)
- Parse `filters` JSON string into `list[dict[str, str]]`; return HTTP 400 on malformed JSON,
  duplicate columns, or invalid filter objects
- Update `InputPreviewResponse` schema with the four new fields (`total_rows`, `filtered_rows`,
  `offset`, `limit`), `has_next`, and deprecated `row_count`
- Offload preview execution to a worker thread so large scans do not block the event loop
- Update backend API tests:
  - Unfiltered pagination (multiple pages)
  - Filtered request returning correct `filtered_rows`
  - `rows` alias backward compatibility
  - Malformed `filters` JSON returns 400
  - Duplicate / unknown filter columns return 400

Dependencies: Ticket 1

Exit criteria:
- Endpoint supports pagination and filtering for both local and S3 sources
- Existing callers using only `rows` continue to work without changes
- Existing callers reading `row_count` continue to work during migration

---

### Ticket 3 — Extend Job Result CSV Preview Endpoints — ✅ DONE (SFBL-64)

Goal: add the same pagination and filtering capability to the job result CSV endpoints.

Scope:

- Update `_preview_csv()` helper in `backend/app/api/jobs.py` to accept `limit`, `offset`,
  `filters: list[dict[str, str]] | None`
- Implement unfiltered path: advance reader `offset` rows, yield `limit + 1` rows, derive
  `has_next`, and avoid newline-count-based totals
- Implement filtered path: full parsed scan, validate filter columns, count exact
  `filtered_rows`, retain only the requested page rows, and optionally populate `total_rows`
- Update the three preview route handlers to accept the new query parameters
  (`limit`, `offset`, `filters`) with the same defaults and constraints as Ticket 2
- Extend the response dict to include `total_rows`, `filtered_rows`, `offset`, `limit`,
  `has_next`, and deprecated `row_count`
- Offload preview execution to a worker thread
- Add backend API tests:
  - Unfiltered pagination for each of the three CSV types
  - Filtered request with matching and non-matching criteria
  - Empty CSV file (header only)
  - Duplicate / unknown filter columns return 400

Dependencies: none (independent of Tickets 1 and 2)

Exit criteria:
- All three job result preview endpoints support pagination and filtering
- Pagination does not rely on inaccurate newline counts

---

### Ticket 4 — Build Shared `CsvPreviewPanel` Component — ✅ DONE (SFBL-65)

Goal: implement the reusable frontend component that all CSV preview contexts will use.

Scope:

- Create `frontend/src/components/ui/CsvPreviewPanel.tsx` with props, state, and behaviour
  as described in the Frontend Component Design section above
- Export from `frontend/src/components/ui/index.ts`
- Filter bar: column selector dropdown (populated from `header`), value text input, add/remove
  filter rows, Apply and Clear buttons
- Table: column headers, data rows with alternating row shading, horizontal scroll for wide files
- Pagination controls: First / Prev / Page indicator / Next / Last, page size selector
- Loading state: spinner overlay on the table area
- Error state: inline error message
- Empty state: contextual message (no data vs. no filter matches)
- Write frontend unit tests covering:
  - Renders header and first page of rows
  - Pagination controls advance and retreat pages
  - Filter bar Apply triggers a new fetch with `activeFilters`
  - Clear Filters resets to page 1 with no filters
  - Duplicate filter columns are prevented in the UI
  - Unknown-total pagination hides/disables "Last" and relies on `has_next`
  - Loading spinner shown while fetch is in-flight
  - Error message shown on fetch failure
  - Empty state shown when `rows` is empty

Dependencies: none

Exit criteria:
- Component is self-contained and testable in isolation using a mock `fetchPage` function
- All described states are covered by tests

---

### Ticket 5 — Integrate `CsvPreviewPanel` into FilesPage — ✅ DONE (SFBL-66)

Goal: replace the bespoke `PreviewTable` component in `FilesPage` with `CsvPreviewPanel`.

Scope:

- Update `filesApi.previewInput()` in `frontend/src/api/endpoints.ts` to accept and pass through
  `CsvFetchParams` (`limit`, `offset`, `filters`) while preserving the current positional
  signature during migration for existing callers such as PlanEditor
- Remove the `PreviewTable`, `PreviewEmpty`, `PreviewLoading`, and `PreviewError` components from
  `FilesPage.tsx` (their responsibilities are absorbed by `CsvPreviewPanel`)
- Render `CsvPreviewPanel` in the preview panel slot, passing:
  - `queryKey` based on `['files', 'preview', source, selectedFile]`
  - `fetchPage` calling `filesApi.previewInput(selectedFile, params, source)`
  - `filename` from the selected file entry
- Reset panel state when `selectedFile` or `source` changes (handled automatically by
  `queryKey` change; no explicit reset needed)
- Update `FilesPage` frontend tests:
  - Preview panel renders paginated data on file selection
  - Changing file resets to page 1
  - Filter bar triggers correct API call
  - Existing header-only callers remain unaffected

Dependencies: Tickets 2 and 4

Exit criteria:
- FilesPage preview behaves identically to the old preview for simple cases
- Users can page through files and filter by column values

---

### Ticket 6 — Integrate `CsvPreviewPanel` into JobDetail — ✅ DONE (SFBL-67)

Goal: replace the inline table in `JobDetail`'s `LogSection` with `CsvPreviewPanel`.

Scope:

- Update `jobsApi.previewSuccessCsv()`, `previewErrorCsv()`, and `previewUnprocessedCsv()` in
  `frontend/src/api/endpoints.ts` to accept and pass through `CsvFetchParams`
- Remove the inline `<table>` from `LogSection` in `JobDetail.tsx`
- Replace with `CsvPreviewPanel` for each of the three log sections, passing:
  - `queryKey` based on `['job-preview-<type>', jobId]`
  - `fetchPage` calling the appropriate `jobsApi.preview*Csv(jobId!, params)` function
  - `filename` from the job record's file path field
- Remove the old "Showing first 25 rows" footer message (now handled generically by
  `CsvPreviewPanel` pagination controls)
- Update `JobDetail` frontend tests:
  - Each log section renders `CsvPreviewPanel`
  - Pagination and filter interactions pass through to the correct API function

Dependencies: Tickets 3 and 4

Exit criteria:
- All three job result CSV sections support pagination and filtering
- JobDetail is visually and functionally consistent with FilesPage previews

---

### Ticket 7 — Remove Deprecated Preview Compatibility Fields — ✅ DONE (SFBL-68)

Goal: remove temporary compatibility shims after all frontend consumers have migrated.

Scope:

- Remove deprecated `row_count` from backend preview DTOs and response models
- Remove any positional preview API overloads kept only for migration
- Update tests to assert only the final response shape and call signatures

Dependencies: Tickets 5 and 6

Exit criteria:
- No runtime code depends on deprecated preview compatibility fields
- Preview APIs expose only the final contract described by this spec

---

## Files Affected

### Backend

| File | Change |
|---|---|
| `backend/app/services/input_storage.py` | Extend `InputPreview` DTO and `preview_file()` on both providers |
| `backend/app/api/utility.py` | Add `limit`, `offset`, `filters` params; update response model |
| `backend/app/api/jobs.py` | Extend `_preview_csv()` and all three preview endpoints |

### Frontend

| File | Change |
|---|---|
| `frontend/src/components/ui/CsvPreviewPanel.tsx` | New shared component |
| `frontend/src/components/ui/index.ts` | Export `CsvPreviewPanel` |
| `frontend/src/api/endpoints.ts` | Update `filesApi.previewInput()` and `jobsApi.preview*Csv()` signatures |
| `frontend/src/api/types.ts` | Add `FilterRule`, `CsvFetchParams`, and `CsvPageResult` types |
| `frontend/src/pages/FilesPage.tsx` | Remove bespoke preview components; render `CsvPreviewPanel` |
| `frontend/src/pages/JobDetail.tsx` | Remove inline table in `LogSection`; render `CsvPreviewPanel` |

### Tests

| File | Change |
|---|---|
| `backend/tests/test_input_storage.py` | Add / update pagination and filter cases |
| `backend/tests/test_utility.py` | Add pagination and filter cases for the preview endpoint |
| `backend/tests/test_jobs.py` | Add pagination and filter cases for the three preview endpoints |
| `frontend/src/__tests__/components/ui/CsvPreviewPanel.test.tsx` | New — full component test coverage |
| `frontend/src/__tests__/pages/FilesPage.test.tsx` | Update preview tests |
| `frontend/src/__tests__/pages/JobDetail.test.tsx` | Update log section tests |

---

## Security Considerations

| Concern | Mitigation |
|---|---|
| Filter value used in file reads | Filters are applied in Python string comparison only; no eval or shell exec |
| Large filter scans blocking the event loop | Preview work is explicitly offloaded to a worker thread from async route handlers |
| Offset beyond end of file | Return an empty `rows` list with correct `offset`; no error raised |
| Malformed `filters` JSON | Return HTTP 400 with a descriptive error message |
| Invalid filter columns | Reject duplicate, blank, or unknown columns with HTTP 400 after header validation |
| Excess memory use during filtering | Count matches in a single pass and retain only the requested page window |
