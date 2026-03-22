# Spec: Refactoring and Test Rationalisation Backlog

## Overview

The application has reached the point where adding features directly on top of the current structure will increase delivery cost and regression risk. This document captures the main refactoring and rationalisation work that should be considered before the next substantial feature wave.

This is a backlog document, not an implementation plan. It does not propose changing behaviour immediately. It identifies where complexity is accumulating, what should be stabilised, and where the current test suite is strong versus where it is thin.

---

## Current State

The codebase is in a workable state, and there is already a decent amount of endpoint and component coverage. The issue is less "lack of code" and more "too much responsibility concentrated in too few places".

The clearest hotspots are:

- `backend/app/services/orchestrator.py` is nearly the entire execution engine in one module at 988 lines.
- `frontend/src/pages/PlanEditor.tsx` is a 1102-line page component combining route concerns, form state, modal state, file browsing, preview state, mutations, and rendering.
- `frontend/src/pages/RunDetail.tsx` combines orchestration-state presentation, retry logic, abort flow, log download UI, and job-level rendering in one 525-line page.
- File discovery, path safety, CSV preview, and row-count logic are implemented in multiple places rather than through one source of truth.

The immediate risk is not that one specific module is broken. The risk is that each new feature will have to touch several loosely coordinated layers, increasing duplication and making behaviour harder to reason about.

---

## Observed Hotspots

### 1. Orchestration logic is carrying too many responsibilities

`backend/app/services/orchestrator.py` currently owns:

- run lifecycle transitions
- step discovery and ordering
- file discovery and partitioning
- Salesforce token acquisition
- partition submission and polling
- result-file persistence
- retry execution flow
- abort handling
- websocket event broadcasting
- final aggregation and error summarisation

This is visible from the module-level flow description and the retry path already starting with significant inline setup in the same file.

Relevant files:

- `backend/app/services/orchestrator.py`
- `backend/tests/test_orchestrator.py`

### 2. Input file behaviour is duplicated across the backend

There are at least three separate places with overlapping file concerns:

- `backend/app/services/csv_processor.py` implements safe file discovery and CSV processing
- `backend/app/api/load_steps.py` implements step preview with its own `glob` and row-count logic
- `backend/app/api/utility.py` implements file listing, safe path resolution, preview, and row counting again

The duplication is not only structural. The implementations also make different assumptions:

- `csv_processor` uses a dedicated traversal check and encoding detection
- `load_steps.py` preview uses raw `glob.glob` plus UTF-8-SIG only
- `utility.py` preview and listing use their own path validation and line-count strategy

This will make future input-source work harder and raises the chance of preview and execution disagreeing for the same file set.

Relevant files:

- `backend/app/services/csv_processor.py`
- `backend/app/api/load_steps.py`
- `backend/app/api/utility.py`

### 3. API routers still contain domain logic that should move behind services

The routers are not just routing and validation layers. They also contain business rules and persistence orchestration:

- run creation and enqueueing in `backend/app/api/load_plans.py`
- abort semantics, summary aggregation, retry setup, and ZIP assembly in `backend/app/api/load_runs.py`
- sequence assignment and reorder logic in `backend/app/api/load_steps.py`

This makes the behaviour harder to reuse and narrows testing to endpoint-level coverage when some of the logic would be easier to verify as service-level units.

Relevant files:

- `backend/app/api/load_plans.py`
- `backend/app/api/load_runs.py`
- `backend/app/api/load_steps.py`

### 4. Frontend page components are too large to evolve cleanly

`PlanEditor.tsx` currently mixes:

- page routing concerns
- plan form state
- step form state
- modal orchestration
- file picker UI
- step preview state
- query/mutation wiring
- validation error extraction
- large render blocks

`RunDetail.tsx` has a similar issue for live execution state and step/job presentation. These files are still understandable, but they are now expensive to change because any new feature adds more state branches and more mocked test setup.

Relevant files:

- `frontend/src/pages/PlanEditor.tsx`
- `frontend/src/pages/RunDetail.tsx`
- `frontend/src/hooks/useLiveRun.ts`

### 5. The frontend/backend live-run model is not fully rationalised

There is a protected WebSocket endpoint at `/ws/runs/{run_id}`, but the frontend live-run experience is still driven by polling in `useLiveRun.ts`. That is not inherently wrong, but the system currently supports two live-update mechanisms without one clear model.

That creates ambiguity around:

- what is authoritative for run progress
- whether websocket events should update React Query cache
- whether polling remains the fallback or the default
- how retry and abort flows should invalidate or reconcile state

Relevant files:

- `backend/app/api/utility.py`
- `frontend/src/hooks/useLiveRun.ts`
- `frontend/src/pages/RunDetail.tsx`

### 6. API contract shaping is becoming manual in several places

The frontend API layer is still manageable, but there are signs of drift:

- manual query-string building in `frontend/src/api/endpoints.ts`
- repeated response-shape knowledge pushed into pages
- direct route-URL assembly for downloads
- no single typed query helper or endpoint factory

This is not urgent on its own, but it will become more painful if more filtered list endpoints or source-aware file APIs are added.

Relevant files:

- `frontend/src/api/endpoints.ts`
- `frontend/src/api/client.ts`
- `frontend/src/pages/PlanEditor.tsx`
- `frontend/src/pages/RunDetail.tsx`

---

## Backlog

## Stage 1: Stabilise Core Boundaries

This is the highest-value refactoring stage. It should happen before another major execution or input-source feature.

### 1. Split the orchestrator into explicit collaborators

Target shape:

- `run_coordinator`
- `step_executor`
- `partition_executor`
- `result_persistence`
- `retry_builder` or equivalent
- `run_event_publisher`

Goals:

- keep `execute_run()` and `execute_retry_run()` as thin entry points
- move pure aggregation and status-transition logic into smaller testable functions
- isolate Salesforce I/O from DB state transitions
- isolate retry-partition construction from retry-run submission

Expected outcome:

- lower regression risk in run execution work
- easier unit tests for step-level and partition-level logic
- clearer ownership for future features such as source-aware execution or partial reruns

### 2. Introduce a single input-file service boundary

Create one backend service layer responsible for:

- path validation
- file listing
- preview generation
- row counting
- pattern discovery
- encoding handling

Then have both:

- `/api/files/input...`
- `/api/load-plans/{plan_id}/steps/{step_id}/preview`

delegate to that shared service.

Goals:

- prevent preview/execution mismatches
- reuse the traversal protections already present in `csv_processor`
- prepare the codebase for source-aware storage work without another round of rewiring

### 3. Move business rules out of routers and into domain services

Initial candidates:

- load-plan duplication
- run creation and queue submission
- run abort
- run summary generation
- run log ZIP generation
- step reordering

Goals:

- keep routers focused on HTTP concerns
- make non-HTTP logic directly testable
- reduce the amount of DB orchestration embedded in endpoint files

---

## Stage 2: Rationalise Frontend Structure

### 4. Break `PlanEditor` into feature components and hooks

Suggested split:

- `PlanForm`
- `StepList`
- `StepEditorModal`
- `FilePicker`
- `PreflightPreviewModal`
- `usePlanEditorState`
- `useStepPreview`

Goals:

- reduce incidental coupling between step editing and plan editing
- make file-picker behaviour reusable
- enable narrower tests that do not require mocking the entire page

### 5. Break `RunDetail` into a live-run container plus presentational sections

Suggested split:

- `RunSummaryCard`
- `RunLogDownloadModal`
- `RunStepPanel`
- `RunJobList`
- `useRunActions`

Goals:

- separate live data wiring from rendering
- make retry/abort actions independently testable
- simplify future additions such as richer progress metrics or event timelines

### 6. Decide on one live-update model

Options:

- keep polling as the primary model and remove websocket-specific complexity from the frontend
- adopt websocket-first updates with polling as a recovery fallback

Either choice is valid. The main need is to make it explicit and consistent.

Goals:

- one authoritative update path
- clear cache invalidation rules
- simpler reasoning in `useLiveRun`

---

## Stage 3: Contract and Model Clean-up

### 7. Normalise backend response and persistence contracts

Candidates for rationalisation:

- `LoadRun.error_summary` is stored as a JSON string rather than a typed structure
- run/job aggregation logic is scattered between persisted totals and frontend-derived totals
- retry-specific behaviour is embedded inside the run model and run routes rather than a dedicated retry domain concept

Goals:

- typed response shapes where the frontend currently has to infer structure
- one clear owner for aggregate totals
- fewer "special case" branches in pages and routes

### 8. Introduce a small API URL/query builder layer in the frontend

Goals:

- remove hand-built query-string assembly
- centralise encoding rules for list and preview endpoints
- make future source-aware file APIs easier to add without touching multiple pages

This can stay lightweight. It does not need a generated client.

---

## Test Suite Assessment

## What Is Already Strong

The following areas already have useful coverage:

- backend CRUD flows for plans, steps, runs, jobs, auth, connections, and utility endpoints
- CSV processing edge cases in `backend/tests/test_csv_processor.py`
- orchestrator happy-path and several failure-path tests in `backend/tests/test_orchestrator.py`
- frontend API client and endpoint URL tests
- frontend page-level smoke and interaction coverage for the main pages

That means the backlog should focus on gaps around coordination logic and contract consistency, not on rebuilding the whole suite.

## Gaps

### 1. Retry-run behaviour is under-tested relative to its complexity

I found no direct tests covering `execute_retry_run` or the `/api/runs/{run_id}/retry-step/{step_id}` endpoint path end to end.

Why this matters:

- retry execution has its own status transitions and partition setup
- it is one of the easiest places for behaviour to drift from the primary run path

Add tests for:

- retry-step endpoint success and validation failures
- retry run creation metadata
- retry orchestration completion and failure handling
- missing source files for retry-partition assembly

### 2. Run log ZIP generation does not appear to have direct endpoint coverage

`/api/runs/{run_id}/logs.zip` is meaningful user-facing behaviour, but there is no dedicated test file asserting archive composition, filtering, or path handling.

Add tests for:

- selected combinations of `success/errors/unprocessed`
- missing files on disk
- archive path layout
- empty archive behaviour

### 3. Step preview coverage is shallow compared with its risk profile

There are basic endpoint tests for preview, but not for the tricky cases that matter once preview and execution share one file service.

Missing coverage includes:

- traversal and escaping patterns through step preview
- encoding edge cases
- disagreement between preview globbing and execution discovery
- hidden-file and nested-directory behaviour

### 4. Orchestrator tests are broad, but the heaviest branches are still difficult to isolate

Current tests do cover the main orchestrator module, but because the implementation is so concentrated, the tests have to work at a fairly integration-heavy level.

After refactoring, add focused tests for:

- step failure threshold evaluation
- abort transitions at each stage boundary
- per-partition status transitions
- event publication sequencing
- final aggregate calculation

### 5. Frontend tests are strong on rendering, weaker on state transitions over time

Examples:

- `useLiveRun` tests assert basic flags and dependencies, but not realistic live transition flows
- there is no frontend integration around websocket-driven updates because the frontend is not using them yet
- page tests rely heavily on mocking endpoint modules rather than verifying cache invalidation and cross-page state behaviour

Add tests for:

- `pending -> running -> terminal` transitions
- abort and retry invalidation behaviour
- download-log option state
- eventual websocket integration if adopted

### 6. Test infrastructure is carrying some structural debt

The backend suite uses file-based SQLite plus several direct DB helpers from tests. That is understandable, but it also means:

- some tests bypass real auth via dependency overrides
- some tests seed DB state directly rather than through public APIs
- orchestration and API tests use different database strategies

This is acceptable now, but if more workflow features are added, introduce clearer test layers:

- fast service/unit tests
- API tests
- orchestrator integration tests

---

## Suggested Order

If this backlog is taken forward, the most sensible order is:

1. Split backend orchestration and file-service responsibilities.
2. Extract frontend page state into smaller hooks/components.
3. Clean up contracts only after the new boundaries are in place.
4. Expand tests around retry, logs ZIP, live updates, and shared file behaviour as the refactors land.

That sequence keeps the refactoring effort aligned with the parts of the system that are already under the most strain.
