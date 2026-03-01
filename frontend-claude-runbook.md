# frontend-claude-runbook.md
## Salesforce Bulk Loader Frontend Runbook (Claude Sonnet) — API-aligned

This runbook is the **single source of truth** for building the React UI **against the APIs that currently exist** in the backend.

It is based on:
- The UI canvas spec (design + UX intent).
- The **Frontend API Audit** (actual endpoint map, gaps, and recommended frontend plan).

---

## 1) Target stack + constraints

### 1.1 Stack
- React + Vite + TypeScript
- Tailwind CSS
- React Router
- TanStack Query (React Query) for fetching/caching/polling
- Headless UI **or** Radix UI for modal/tabs/dropdowns
- Optional: zod (runtime validation)

### 1.2 Key constraints from backend reality
- Canonical routes often include a trailing slash (e.g. `/api/connections/`). The frontend must tolerate this.
- **Plans are `/api/load-plans/`**, not `/api/plans`.
- **Start run is plan-scoped**: `POST /api/load-plans/{plan_id}/run`.
- **No plan-level preview**: preview exists per step: `POST /api/load-plans/{plan_id}/steps/{step_id}/preview`.
- **No run steps endpoint**: `/api/runs/{id}/steps` is missing. Build step accordion by combining plan steps + run jobs.
- **Job detail is global**: `GET /api/jobs/{job_id}` and download endpoints per job.
- Files endpoints are **input-scoped**: `/api/files/input`.
- CORS dev allowlist is currently `localhost:3000` only; Vite is `5173` by default, so use a Vite proxy (recommended) or update backend CORS.

---

## 2) Frontend routes (UI)

Keep the UI routes stable, even if the backend routes differ.

- Dashboard: `/`
- Connections: `/connections`
- Load Plans list: `/plans`
- Load Plan editor: `/plans/:id`
- Runs list: `/runs`
- Run detail: `/runs/:id`
- Job detail: `/runs/:runId/jobs/:jobId` (UI nested route; API is global `/api/jobs/{jobId}`)
- Files: `/files`

---

## 3) API contract to build against (actual endpoints)

### 3.1 Connections
- `GET /api/connections/`
- `POST /api/connections/`
- `PUT /api/connections/{connection_id}`
- `DELETE /api/connections/{connection_id}`
- `POST /api/connections/{connection_id}/test`
  - Always returns HTTP 200; check `success: boolean` in JSON body.

### 3.2 Load Plans + Steps
Plans:
- `GET /api/load-plans/`
- `POST /api/load-plans/`
- `GET /api/load-plans/{plan_id}`
- `PUT /api/load-plans/{plan_id}`
- `DELETE /api/load-plans/{plan_id}` (if present in your backend)

Steps:
- `POST /api/load-plans/{plan_id}/steps`
- `PUT /api/load-plans/{plan_id}/steps/{step_id}`
- `DELETE /api/load-plans/{plan_id}/steps/{step_id}`
- `POST /api/load-plans/{plan_id}/steps/reorder`
- `POST /api/load-plans/{plan_id}/steps/{step_id}/preview`

**Preflight (frontend-implemented)**  
Because there is no plan-level preview, implement Preflight as:
1) `GET /api/load-plans/{plan_id}` to get steps
2) For each step, call `POST /api/load-plans/{plan_id}/steps/{step_id}/preview`
3) Aggregate results into a single modal/panel

### 3.3 Runs
- `GET /api/runs/` with filters:
  - `plan_id`
  - `run_status`
  - `started_after`
  - `started_before`
- `GET /api/runs/{run_id}`
  - Note: response may contain `jobs`, but mapping might be unreliable; do not rely solely on it.
- `GET /api/runs/{run_id}/summary` (if implemented; otherwise omit)
- `GET /api/runs/{run_id}/jobs?step_id=&job_status=`
- `POST /api/runs/{run_id}/abort` (may return 409 if not abortable)

Start run:
- `POST /api/load-plans/{plan_id}/run`

### 3.4 Jobs (global)
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/success-csv`
- `GET /api/jobs/{job_id}/error-csv`
- `GET /api/jobs/{job_id}/unprocessed-csv`

**Job tabs (MVP)**
- Overview (job fields + counts)
- Raw SF payload (render `sf_api_response` via JSON viewer, if present)
- Downloads (direct links; do not buffer large files in JS)

### 3.5 Files (input)
- `GET /api/files/input`
- `GET /api/files/input/{filename}/preview?rows=25`

### 3.6 WebSocket (nice-to-have)
- `WS /ws/runs/{run_id}`

---

## 4) TypeScript API types (baseline)

Use these as the canonical frontend types (adjust field names if backend differs):

```ts
export type RunStatus =
  | "pending"
  | "running"
  | "completed"
  | "completed_with_errors"
  | "failed"
  | "aborted";

export type JobStatus =
  | "pending"
  | "uploading"
  | "upload_complete"
  | "in_progress"
  | "job_complete"
  | "failed"
  | "aborted";

export interface ApiValidationError {
  type: string;
  loc: Array<string | number>;
  msg: string;
  input?: unknown;
}

export interface ApiError {
  status: number;
  message: string;
  detail?: string | ApiValidationError[];
  code?: string;
}

export interface Connection {
  id: string;
  name: string;
  instance_url: string;
  login_url: string;
  client_id: string;
  username: string;
  is_sandbox: boolean;
  created_at: string;
  updated_at: string;
}

export interface ConnectionCreate {
  name: string;
  instance_url: string;
  login_url: string;
  client_id: string;
  private_key: string;
  username: string;
  is_sandbox?: boolean;
}

export interface ConnectionTestResponse {
  success: boolean;
  message: string;
  instance_url?: string | null;
}

export type Operation = "insert" | "update" | "upsert" | "delete";

export interface LoadStep {
  id: string;
  load_plan_id: string;
  sequence: number;
  object_name: string;
  operation: Operation;
  external_id_field?: string | null;
  csv_file_pattern: string;
  partition_size: number;
  assignment_rule_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface LoadPlan {
  id: string;
  connection_id: string;
  name: string;
  description?: string | null;
  abort_on_step_failure: boolean;
  error_threshold_pct: number;
  max_parallel_jobs: number;
  created_at: string;
  updated_at: string;
}

export interface LoadPlanDetail extends LoadPlan {
  load_steps: LoadStep[];
}

export interface LoadRun {
  id: string;
  load_plan_id: string;
  status: RunStatus;
  started_at?: string | null;
  completed_at?: string | null;
  total_records?: number | null;
  total_success?: number | null;
  total_errors?: number | null;
  initiated_by?: string | null;
  error_summary?: string | null;
}

export interface JobRecord {
  id: string;
  load_run_id: string;
  load_step_id: string;
  sf_job_id?: string | null;
  partition_index: number;
  status: JobStatus;
  records_processed?: number | null;
  records_failed?: number | null;
  success_file_path?: string | null;
  error_file_path?: string | null;
  unprocessed_file_path?: string | null;
  sf_api_response?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  error_message?: string | null;
}

export interface StepPreviewInfo {
  filename: string;
  row_count: number;
}

export interface StepPreviewResponse {
  pattern: string;
  matched_files: StepPreviewInfo[];
  total_rows: number;
}

export interface InputFileInfo {
  filename: string;
  size_bytes: number;
}

export interface InputFilePreview {
  filename: string;
  header: string[];
  rows: Record<string, string>[];
  row_count: number;
}
```

---

## 5) Dev setup: Vite proxy (recommended)

To avoid CORS issues (backend allows `localhost:3000` but Vite uses `5173`), add a proxy:

**vite.config.ts**
- Proxy `/api` → `http://localhost:8000`
- Proxy `/ws` → `ws://localhost:8000`

Also allow overriding backend URL via env (`VITE_BACKEND_URL`).

---

## 6) UI behaviour rules (non-negotiable)

### 6.1 Loading/empty/error states everywhere
Every page must handle:
- loading
- empty (no items)
- error (API failure)

### 6.2 Downloads must not buffer large CSVs
Use direct `<a href>` links to download endpoints. Only fetch small previews.

### 6.3 Run monitoring: polling-first
MVP uses polling:
- While run status is `pending` or `running`, poll:
  - `GET /api/runs/{id}`
  - `GET /api/runs/{id}/jobs`
- Interval: 3000ms (adjustable)
- Stop when status is terminal.

### 6.4 Run mission control step accordion construction (since `/runs/{id}/steps` is missing)
- Fetch plan detail: `GET /api/load-plans/{run.load_plan_id}` → `load_steps`
- Fetch jobs: `GET /api/runs/{run_id}/jobs`
- Group jobs by `load_step_id`
- Render step panels in `sequence` order from plan

### 6.5 Connection test semantics
`POST /api/connections/{id}/test` returns HTTP 200 even on failure.
- The UI must display success/failure based on `response.success`.

### 6.6 Abort semantics
`POST /api/runs/{id}/abort` can return 409.
- Show a friendly banner: “Run is not abortable (already finished or abort in progress).”

---

## 7) Implementation milestones (one prompt per milestone)

### Milestone 1 — Scaffold + routing + base UI + proxy
Deliverables:
- Vite React TS + Tailwind
- Router + AppShell nav
- Base UI kit: Button, Card, Badge, Progress, DataTable, Modal, Tabs, Toast, EmptyState
- Vite proxy for /api and /ws
- Placeholder pages for all routes

### Milestone 2 — API client + types + React Query setup
Deliverables:
- `src/api/client.ts` fetch wrapper and `ApiError` mapping:
  - 422 → `detail` array
  - non-422 → `{detail: string}`
- `src/api/types.ts` and `src/api/endpoints.ts`
- React Query provider and devtools

### Milestone 3 — Connections CRUD + Test
Deliverables:
- `/connections` page with list + editor
- Create/update/delete
- Test button with result panel + diagnostics

### Milestone 4 — Plans list + Plan editor + Steps + Preflight (aggregated)
Deliverables:
- `/plans` list
- `/plans/:id` editor:
  - plan metadata
  - step CRUD (create/update/delete)
  - reorder
  - per-step preview panel
  - Preflight modal aggregates all step previews
- Start run button:
  - `POST /api/load-plans/{plan_id}/run` then navigate to `/runs/{runId}`

### Milestone 5 — Runs list + Run detail (mission control) + polling
Deliverables:
- `/runs` list with filters using `/api/runs/`
- `/runs/:id` detail:
  - sticky summary header (status, progress, counts, elapsed)
  - step accordion built from plan steps + grouped jobs
  - abort with confirm modal + 409 handling
- `useLiveRun.ts` polling hook

### Milestone 6 — Job detail (downloads-first)
Deliverables:
- `/runs/:runId/jobs/:jobId` page
- Tabs: Overview, Raw SF payload, Downloads
- Download buttons link to CSV endpoints
- Show “Not available” if 404

### Milestone 7 — Files browser + preview
Deliverables:
- `/files` list from `/api/files/input`
- Preview panel from `/api/files/input/{filename}/preview?rows=25`
- Horizontal scroll for wide tables

### Milestone 8 — Nice-to-haves
- WebSocket updates for run detail (`/ws/runs/{id}`) with reconnect banner + fallback to polling
- Zip download all logs (if backend adds)
- Step-level error summaries (if backend adds parsed endpoints)
- Drag-drop reorder

---

## 8) Copy/paste prompts for Claude Sonnet

### Prompt 0 — Global instruction (use once)
```text
You are Claude Sonnet working inside a codebase. Implement the frontend UI for the Salesforce Bulk Loader app.

Obey these rules:
- Use React + Vite + TypeScript + Tailwind.
- Use React Router and TanStack Query.
- Build against the EXISTING backend endpoints: /api/load-plans, /api/runs, /api/jobs, /api/files/input, /api/connections (note trailing slash).
- Do not invent /api/plans or nested /runs/{runId}/jobs/{jobId} endpoints. Adapt the UI to real endpoints.
- Polling-first for run detail (3000ms while running).
- Downloads must be direct links (do not buffer CSVs in JS).
- Every page must have loading/empty/error states.
- Keep UI “SLDS-ish” using Tailwind tokens and simple components; do NOT import full SLDS.
- Make changes incrementally, ensure build runs after each milestone.
```

### Prompt 1 — Milestone 1 scaffold + proxy
```text
Implement Milestone 1 from frontend-claude-runbook.md.

Scope:
- Vite React TS + Tailwind setup
- Router + AppShell + nav
- Base UI components (Button/Card/Badge/Progress/DataTable/Modal/Tabs/Toast/EmptyState)
- Vite proxy for /api and /ws to backend at http://localhost:8000
- Placeholder pages for all routes

Done criteria:
- npm run dev works
- navigation works
- proxy works
```

### Prompt 2 — Milestone 2 API layer
```text
Implement Milestone 2 from frontend-claude-runbook.md.

Scope:
- src/api/client.ts with ApiError mapping:
  - 422 detail array
  - {detail: string} message
- src/api/types.ts
- src/api/endpoints.ts for all endpoints in section 3
- Wire React Query provider + devtools

Done criteria:
- Types compile
- One placeholder page (Dashboard) can call /api/health or /api/runs/ and render.
```

(Then proceed milestone-by-milestone using the Milestone sections.)

---

## 9) Acceptance criteria (MVP)
- Create/edit/test connections works; test shows logical failure without relying on HTTP status.
- Create/edit plans and steps works; preview per step works; preflight aggregates previews.
- Start run works from plan editor.
- Runs list filters correctly.
- Run detail shows step/job progress and updates via polling.
- Abort run works with safe confirmation and 409 handling.
- Job detail shows metadata and supports CSV downloads.
- Files list and preview work reliably.

---

## 10) Known backend gaps (do not block MVP)
These are future backend enhancements that would simplify the UI but are not required:
- Alias endpoints for `/api/plans`, `/api/files`
- Plan-level preview endpoint
- `/api/runs/{id}/steps`
- Nested run/job detail endpoints
- Parsed error pagination endpoints
- Unified download endpoint with `type=` query parameter
- CORS allowlist includes `http://localhost:5173`

