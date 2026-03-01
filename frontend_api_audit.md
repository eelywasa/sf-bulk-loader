# Frontend API Audit & Implementation Plan

## 1) Codebase orientation

### Backend framework and API structure
- **Framework**: FastAPI (Python), with app bootstrap in `backend/app/main.py`.
- **Routing**: Modular `APIRouter` modules in `backend/app/api/`.
  - `connections.py` → `/api/connections`
  - `load_plans.py` + `load_steps.py` → `/api/load-plans` and nested step routes
  - `load_runs.py` → `/api/runs`
  - `jobs.py` → mixed run-scoped and global job routes
  - `utility.py` → files, health, and websocket route mounting
- **Request/response models**: Pydantic schemas in `backend/app/schemas/*`.
- **Persistence**: SQLAlchemy async ORM models in `backend/app/models/*`.

### API docs / OpenAPI
- OpenAPI is available via FastAPI defaults:
  - `GET /openapi.json`
  - `GET /docs`
  - `GET /redoc`
- No custom OpenAPI generation logic is implemented.

### Repo structure summary
- `backend/app/main.py`: FastAPI entrypoint and router mounting.
- `backend/app/config.py`: env-driven settings (CORS env mode, directories, Salesforce polling defaults).
- `backend/app/api/`: HTTP/WebSocket endpoints.
- `backend/app/schemas/`: DTO contract.
- `backend/app/models/`: DB tables + enums.
- `backend/tests/`: API and service tests.
- `salesforce-bulk-loader-spec.md`: product + endpoint specification.
- **No existing `frontend/` folder is present in this repo snapshot.**

---

## 2) API contract verification (UI-targeted)

Assumption: target UI contract is the endpoint list in your prompt (`/api/plans`, run/job detail nested routes, `/api/files`, etc.), while this repo is source-of-truth actual behavior.

### Spec endpoint → actual endpoint mapping

| Spec endpoint | Actual endpoint(s) | Match? | Diff / Notes | Suggested minimal fix |
|---|---|---:|---|---|
| `GET /api/connections` | `GET /api/connections/` | ⚠️ partial | Trailing slash canonical route (FastAPI handles redirect in many clients). | Add non-trailing alias or normalize frontend base path helper. |
| `POST /api/connections` | `POST /api/connections/` | ⚠️ partial | Same trailing slash nuance. | Same as above. |
| `PUT /api/connections/{id}` | `PUT /api/connections/{connection_id}` | ✅ | Path param name differs only semantically. | None. |
| `DELETE /api/connections/{id}` | `DELETE /api/connections/{connection_id}` | ✅ | — | None. |
| `POST /api/connections/{id}/test` | `POST /api/connections/{connection_id}/test` | ✅ | Returns `200` with `{success:false}` even on auth/API failures; transport errors only for not-found. | Keep for MVP; optionally return 4xx/5xx for operational failures if UI needs hard-fail semantics. |
| `GET /api/plans` | `GET /api/load-plans/` | ❌ | Prefix mismatch (`plans` vs `load-plans`). | Add alias router `/api/plans` mapped to same handlers. |
| `POST /api/plans` | `POST /api/load-plans/` | ❌ | Prefix mismatch. | Alias route. |
| `GET /api/plans/{id}` | `GET /api/load-plans/{plan_id}` | ❌ | Prefix mismatch. | Alias route. |
| `PUT /api/plans/{id}` | `PUT /api/load-plans/{plan_id}` | ❌ | Prefix mismatch. | Alias route. |
| `POST /api/plans/{id}/preview` | **No plan-level preview**; exists `POST /api/load-plans/{plan_id}/steps/{step_id}/preview` | ❌ | Feature exists at step-level only. | Add plan-level preflight endpoint aggregating step previews or adapt UI to call step preview per step. |
| `GET /api/runs` | `GET /api/runs/` | ⚠️ partial | Supported filters: `plan_id`, `run_status`, `started_after`, `started_before`. | Document filters in UI client. |
| `GET /api/runs/{id}` | `GET /api/runs/{run_id}` | ⚠️ partial | Route exists, but response model defines `jobs` while ORM relationship is `job_records`; potential empty/missing `jobs` risk depending on serialization mapping. | Add schema alias (`jobs` ← `job_records`) or transform in endpoint. |
| `GET /api/runs/{id}/steps` | **Missing** | ❌ | No run-step breakdown endpoint. | Add endpoint joining run jobs grouped by load step (or expose run summary + step metadata). |
| `GET /api/runs/{id}/jobs?step_id=...` | `GET /api/runs/{run_id}/jobs?step_id=&job_status=` | ✅ | Supports requested `step_id` and extra `job_status`. | None. |
| `POST /api/runs` (start run) | `POST /api/load-plans/{plan_id}/run` | ❌ | Start-run is plan-scoped, not runs collection. | Add `POST /api/runs` wrapper accepting `plan_id`. |
| `POST /api/runs/{id}/abort` | `POST /api/runs/{run_id}/abort` | ✅ | Returns 409 if run not abortable. | None. |
| `GET /api/runs/{run_id}/jobs/{job_id}` | `GET /api/jobs/{job_id}` | ❌ | Missing run-scoped job detail route. | Add nested alias route validating `run_id` ownership. |
| `GET /api/runs/{run_id}/jobs/{job_id}/errors?limit=&offset=` | **Missing** | ❌ | No parsed errors endpoint; only CSV download endpoints. | Add parsed CSV pagination endpoint (`items`, `total`, `limit`, `offset`). |
| `GET /api/runs/{run_id}/jobs/{job_id}/success_sample` | **Missing** | ❌ | Not implemented. | Add small sample endpoint from success CSV. |
| `GET /api/runs/{run_id}/jobs/{job_id}/raw` | **Missing** | ❌ | No raw SF response endpoint except `sf_api_response` field in job detail. | Add endpoint returning parsed JSON from `sf_api_response`. |
| `GET /api/runs/{run_id}/jobs/{job_id}/download?type=error|success|unprocessed` | `GET /api/jobs/{job_id}/success-csv`, `/error-csv`, `/unprocessed-csv` | ❌ | Different path and download selection mechanism. | Add single `download` endpoint with `type` query param delegating to existing handlers. |
| `GET /api/files` | `GET /api/files/input` | ❌ | Path mismatch and naming tied to “input”. | Add `/api/files` alias returning same payload. |
| `GET /api/files/{path}/preview?rows=25` | `GET /api/files/input/{filename}/preview?rows=` | ❌ | Path shape differs (`{path}` vs input-scoped filename). | Add alias and keep basename sanitization. |
| `WS /ws/runs/{id}` | `WS /ws/runs/{run_id}` | ✅ | Exists; sends `connected` event + ping/pong keepalive. | None. |

### Response shape and status code notes
- Validation errors are FastAPI default `422` with `detail` array.
- Most business errors are `HTTPException(detail="...")`, giving `{ "detail": "..." }` (string detail).
- Success statuses generally align to REST conventions (`201` creates, `204` deletes).
- `POST /api/connections/{id}/test` intentionally returns `200` even on unsuccessful test (encoded in body).
- CSV downloads return `FileResponse` (`text/csv`) and `404` when unavailable/missing.

### Error shape consistency
- **Mostly consistent for operational errors**: `{detail: string}`.
- **Validation mismatch**: FastAPI 422 uses array/object detail shape.
- Recommendation: define a canonical error envelope for non-2xx responses (e.g. `{"error":{"code":"...","message":"...","fields":[]}}`) and optionally custom handler to normalize validation failures.

### Pagination conventions
- No general pagination envelope for list endpoints (`connections`, `plans`, `runs`, `jobs`, `files`).
- Requested paginated endpoint (`job errors list`) is not implemented yet.
- Recommendation for frontend scalability: `{items, total, limit, offset}` for future paged resources.

---

## 3) Frontend readiness checklist

### CORS for separate Vite dev server
- Current CORS allows only `http://localhost:3000` in development.
- Vite default dev port is `5173`.
- **Gap**: default Vite SPA will fail browser CORS unless proxied or backend allowlist updated.
- Minimal backend fix: allow configurable origins list (env var), including `http://localhost:5173`.

### Auth assumptions
- No auth middleware present; APIs are open in current implementation.
- Connection test uses stored Salesforce credentials internally; UI appears local/admin-trusted.
- Frontend can assume no bearer token for MVP.

### Large download handling
- Download endpoints stream files from disk with `FileResponse` (good for large files).
- UI should use direct browser navigation/link for downloads where possible, not JSON fetch buffering.

### Run polling feasibility and rate limits
- Polling feasible via `GET /api/runs/{id}` and `/api/runs/{id}/jobs`.
- Server-side Salesforce polling settings exist (`sf_poll_interval_initial`, `sf_poll_interval_max`) but no API rate limiter for UI endpoints.
- Suggested UI polling: 2–5s while active, exponential backoff on failures; stop when terminal status.
- WebSocket `/ws/runs/{id}` exists; can be phase-2 enhancement over polling.

---

## 4) Proposed typed API client model (based on actual backend)

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

export interface LoadRunDetail extends LoadRun {
  jobs: JobRecord[]; // backend should ensure mapping is reliable
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

Minimal backend standardization suggestions:
1. Add compatibility aliases for `/api/plans`, `/api/files`, and nested run/job paths.
2. Normalize `LoadRunDetail.jobs` mapping from ORM relationship (`job_records`).
3. Add one unified download endpoint with query type.
4. Add normalized error envelope + handler for 422.

---

## 5) Concrete frontend implementation plan (MVP-first)

### Milestone 1 — App shell + routing + API core
**Build**
- `src/app/AppShell.tsx` (top nav, page container)
- `src/app/router.tsx` (routes)
- `src/lib/api/client.ts` (fetch wrapper + `ApiError` mapping)
- `src/lib/api/types.ts` (types above)

**API calls used**
- Health check optional: `GET /api/health`

**Tricky states**
- Global API error toast
- Offline/backend unavailable state

**Backend gaps**
- None blocking.

### Milestone 2 — Connections CRUD
**Build**
- `/connections` page
- `ConnectionList`, `ConnectionForm`, `ConnectionTestButton`

**API calls**
- `GET/POST/PUT/DELETE /api/connections/`
- `POST /api/connections/{id}/test`

**Tricky states**
- Secret handling (private key never echoed)
- Test endpoint returns `200` on logical failure; UI must inspect `success` boolean.

**Backend gaps**
- Optional: return richer test diagnostics.

### Milestone 3 — Plans list/editor + step preflight
**Build**
- `/plans` list page
- `/plans/:id` editor page
- components: `PlanForm`, `StepTable`, `StepEditorDrawer`, `StepPreviewPanel`

**API calls**
- `GET/POST/PUT/DELETE /api/load-plans/`
- `POST /api/load-plans/{plan_id}/steps`
- `PUT/DELETE /api/load-plans/{plan_id}/steps/{step_id}`
- `POST /api/load-plans/{plan_id}/steps/reorder`
- `POST /api/load-plans/{plan_id}/steps/{step_id}/preview`

**Tricky states**
- Empty plan (no steps)
- Reorder optimistic update rollback on failure
- Preview with zero matched files

**Backend gaps**
- If UI spec strictly needs `/api/plans/{id}/preview`, add alias endpoint.

### Milestone 4 — Runs list + run detail (polling first)
**Build**
- `/runs` list page with filters
- `/runs/:id` detail with summary/job table

**API calls**
- `GET /api/runs/?plan_id=&run_status=&started_after=&started_before=`
- `GET /api/runs/{id}`
- `GET /api/runs/{id}/summary`
- `GET /api/runs/{id}/jobs?step_id=&job_status=`
- `POST /api/load-plans/{plan_id}/run`
- `POST /api/runs/{id}/abort`

**Tricky states**
- Polling lifecycle and terminal-status detection
- Abort conflict handling (`409`)

**Backend gaps**
- Consider adding `POST /api/runs` alias for spec parity.

### Milestone 5 — Job detail tabs + downloads
**Build**
- `/runs/:runId/jobs/:jobId`
- tabs: Overview, Raw API payload, Errors sample, Success sample, Downloads

**API calls (actual today)**
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/success-csv`
- `GET /api/jobs/{job_id}/error-csv`
- `GET /api/jobs/{job_id}/unprocessed-csv`

**Tricky states**
- Missing files (404) vs not generated yet
- Large CSV downloads via direct URL

**Backend gaps (important for UI contract)**
- Missing nested and sample endpoints (`/runs/{run_id}/jobs/{job_id}/...`).

### Milestone 6 — Files browser + preview
**Build**
- `/files` browser page
- `FileList`, `FilePreviewTable`

**API calls**
- `GET /api/files/input`
- `GET /api/files/input/{filename}/preview?rows=25`

**Tricky states**
- CSV parse/display variability by headers
- Very wide tables (horizontal scroll)

**Backend gaps**
- Add `/api/files` alias if strict spec path required.

---

## 6) Optional small, PR-sized backend improvements

1. **CORS origin list from env** (`BACKEND_CORS_ORIGINS`) and include Vite default 5173.
2. **Alias routes for spec parity**:
   - `/api/plans` → `/api/load-plans`
   - `/api/files` → `/api/files/input`
   - nested run/job paths in addition to `/api/jobs/*`.
3. **Fix `LoadRunDetail.jobs` mapping** explicitly in endpoint response serialization.
4. **Unify download API** to `/download?type=` while retaining old routes for backward compatibility.
5. **Add paginated job error sample endpoint** with `{items,total,limit,offset}`.
6. **Normalize error envelope** (including 422 handler) to simplify frontend error handling.

