# RBAC Permission Matrix

**Spec:** SFBL-185 / Epic B  
**Last updated:** 2026-04-21  
**Canonical source:** `docs/specs/rbac-permission-matrix.yml`  
**Enforcement proof:** `backend/tests/test_permission_matrix.py`

---

## Overview

sf-bulk-loader uses a **profile-based RBAC model** with granular permission keys.
Three system profiles are seeded on startup: `admin`, `operator`, `viewer`.
A fourth virtual profile `desktop` is used in desktop mode (`auth_mode=none`) and
holds all permission keys — no enforcement cost in that mode.

For the full design rationale see `docs/specs/implemented/multi-user-rbac.md` §5 (archived).

> **Note:** The `docs/specs/rbac-permission-matrix.yml` file is the single source of
> truth for profile permission assignments. The table below and the backend test suite
> are both derived from it. Run `backend/tests/test_permission_matrix.py::test_matrix_doc_matches_seed_data`
> to verify this document is in sync with the seed data.

---

## Permission key matrix

| Permission key | admin | operator | viewer | Description |
|---|:---:|:---:|:---:|---|
| `connections.view` | ✓ | ✓ | ✓ | List connections + see connection names |
| `connections.view_credentials` | ✓ | ✗ | ✗ | See client_id (consumer key); the `ConnectionResponse` shape vs `ConnectionPublic` |
| `connections.manage` | ✓ | ✗ | ✗ | Create / edit / delete connections |
| `plans.view` | ✓ | ✓ | ✓ | View load plans and steps |
| `plans.manage` | ✓ | ✓ | ✗ | Create / edit / delete / duplicate load plans |
| `runs.view` | ✓ | ✓ | ✓ | View load runs and job summaries |
| `runs.execute` | ✓ | ✓ | ✗ | Trigger runs and retry failed steps |
| `runs.abort` | ✓ | ✓ | ✗ | Abort an in-flight run |
| `files.view` | ✓ | ✓ | ✓ | Browse files list and metadata |
| `files.view_contents` | ✓ | ✓ | ✗ | Preview / download raw file contents |
| `users.manage` | ✓ | ✗ | ✗ | Full user management (invite, edit, deactivate) |
| `system.settings` | ✓ | ✗ | ✗ | View / change system settings |

### Notes

- **Operator** can manage load plans (`plans.manage`) — the rationale is that
  operators typically own the data-load configuration day-to-day.
- **Operator** cannot see Salesforce connection credentials (`connections.view_credentials`) —
  this protects `client_id` (consumer key). The `login_url`, `username`, and `is_sandbox`
  fields are exposed in the public shape (`ConnectionPublic`) to all users with `connections.view`.
- **Viewer** is view-only: no run execution, no file preview, no plan editing.

---

## Route permission guards

> **Regenerate this section when routes change.** Derived from `backend/app/api/` by inspection.
> The backend test suite (`test_permission_matrix.py`) provides automated enforcement proof.

### Connections (`/api/connections/`)

| Method | Path | Permission key | Notes |
|---|---|---|---|
| `GET` | `/api/connections/` | `connections.view` | List — returns public shape only |
| `POST` | `/api/connections/` | `connections.manage` | Create |
| `GET` | `/api/connections/{id}` | `connections.view` | Detail — shape depends on `connections.view_credentials` |
| `PUT` | `/api/connections/{id}` | `connections.manage` | Update |
| `DELETE` | `/api/connections/{id}` | `connections.manage` | Delete |
| `GET` | `/api/connections/{id}/objects` | `connections.view` | List SF SObjects |
| `POST` | `/api/connections/{id}/test` | `connections.view` | Test connectivity |

### Load Plans (`/api/load-plans/`)

| Method | Path | Permission key | Notes |
|---|---|---|---|
| `GET` | `/api/load-plans/` | `plans.view` | List all plans |
| `POST` | `/api/load-plans/` | `plans.manage` | Create plan |
| `GET` | `/api/load-plans/{id}` | `plans.view` | Get plan detail |
| `PUT` | `/api/load-plans/{id}` | `plans.manage` | Update plan |
| `DELETE` | `/api/load-plans/{id}` | `plans.manage` | Delete plan |
| `POST` | `/api/load-plans/{id}/duplicate` | `plans.manage` | Clone plan |
| `POST` | `/api/load-plans/{id}/run` | `runs.execute` | Trigger a run |

### Load Steps (`/api/load-plans/{plan_id}/steps`)

| Method | Path | Permission key | Notes |
|---|---|---|---|
| `POST` | `/api/load-plans/{id}/steps` | `plans.manage` | Add step |
| `POST` | `/api/load-plans/{id}/steps/reorder` | `plans.manage` | Reorder steps |
| `PUT` | `/api/load-plans/{id}/steps/{step_id}` | `plans.manage` | Update step |
| `DELETE` | `/api/load-plans/{id}/steps/{step_id}` | `plans.manage` | Delete step |
| `POST` | `/api/load-plans/{id}/validate-soql` | `plans.view` | Validate SOQL (read) |
| `POST` | `/api/load-plans/{id}/steps/{step_id}/preview` | `plans.view` | Preview step |

### Load Runs (`/api/runs/`)

| Method | Path | Permission key | Notes |
|---|---|---|---|
| `GET` | `/api/runs/` | `runs.view` | List runs (router-level) |
| `GET` | `/api/runs/{id}` | `runs.view` | Get run detail |
| `POST` | `/api/runs/{id}/abort` | `runs.abort` | Abort run |
| `GET` | `/api/runs/{id}/logs.zip` | `runs.view` + `files.view_contents` | ZIP bundles per-job success/error/unprocessed CSVs — same PII as the individual CSV endpoints, so gated on both (SFBL-206) |
| `POST` | `/api/runs/{id}/retry-step/{step_id}` | `runs.execute` | Retry failed step |

### Jobs (`/api/jobs/`, `/api/runs/{id}/jobs`)

| Method | Path | Permission key | Notes |
|---|---|---|---|
| `GET` | `/api/runs/{id}/jobs` | `runs.view` | List jobs for a run |
| `GET` | `/api/jobs/{id}` | `runs.view` | Get job detail |
| `GET` | `/api/jobs/{id}/success-csv` | `runs.view` + `files.view_contents` | Download success CSV (SFBL-206) |
| `GET` | `/api/jobs/{id}/error-csv` | `runs.view` + `files.view_contents` | Download error CSV (SFBL-206) |
| `GET` | `/api/jobs/{id}/unprocessed-csv` | `runs.view` + `files.view_contents` | Download unprocessed CSV (SFBL-206) |
| `GET` | `/api/jobs/{id}/success-csv/preview` | `runs.view` + `files.view_contents` | Preview success rows (SFBL-206) |
| `GET` | `/api/jobs/{id}/error-csv/preview` | `runs.view` + `files.view_contents` | Preview error rows (SFBL-206) |
| `GET` | `/api/jobs/{id}/unprocessed-csv/preview` | `runs.view` + `files.view_contents` | Preview unprocessed rows (SFBL-206) |

### Files (`/api/files/`)

| Method | Path | Permission key | Notes |
|---|---|---|---|
| `GET` | `/api/files/input` | `files.view` | List input files |
| `GET` | `/api/files/input/{path}/preview` | `files.view_contents` | Preview input CSV |
| `GET` | `/api/files/output` | `files.view` | List output files |
| `GET` | `/api/files/output/{path}/preview` | `files.view_contents` | Preview output CSV |

### Settings (`/api/settings/`)

| Method | Path | Permission key | Notes |
|---|---|---|---|
| `GET` | `/api/settings` | `system.settings` | All categories |
| `GET` | `/api/settings/{category}` | `system.settings` | Single category |
| `PATCH` | `/api/settings/{category}` | `system.settings` | Update settings |

### Admin (`/api/admin/`)

| Method | Path | Permission key | Notes |
|---|---|---|---|
| `GET` | `/api/admin/users` | `users.manage` | List users (Epic C) |
| `POST` | `/api/admin/users` | `users.manage` | Invite user (Epic C) |
| `GET` | `/api/admin/users/{id}` | `users.manage` | User detail (Epic C) |
| `PUT` | `/api/admin/users/{id}` | `users.manage` | Update user (Epic C) |
| `POST` | `/api/admin/users/{id}/deactivate` | `users.manage` | Deactivate (Epic C) |
| `POST` | `/api/admin/users/{id}/reactivate` | `users.manage` | Reactivate (Epic C) |
| `DELETE` | `/api/admin/users/{id}` | `users.manage` | Soft-delete (Epic C) |

### Authenticated-only (no permission key — any authenticated user)

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/auth/me` | Returns current user + permissions |
| `GET` | `/api/auth/config` | Returns auth configuration |
| `POST` | `/api/auth/logout` | Logout |
| `POST` | `/api/me/password` | Change own password |
| `GET` | `/api/me/login-history` | Own sign-in history |
| `GET` | `/api/notification-subscriptions` | Own notification subscriptions |
| `POST` | `/api/notification-subscriptions` | Create subscription |
| `GET,PUT,DELETE` | `/api/notification-subscriptions/{id}` | Manage own subscription |

### Unauthenticated (public)

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/runtime` | Distribution profile config for frontend |
| `GET` | `/api/health` | Basic health check |
| `GET` | `/api/health/live` | Liveness probe |
| `GET` | `/api/health/ready` | Readiness probe |
| `GET` | `/api/health/dependencies` | Dependency health |
| `POST` | `/api/auth/login` | Login |
| `POST` | `/api/auth/forgot-password` | Request password reset |
| `POST` | `/api/auth/reset-password/{token}` | Confirm password reset |

---

## Frontend enforcement

The frontend mirrors backend permission enforcement via:

- `usePermission(key)` hook — returns `boolean` from `AuthContext.permissions`
- `<PermissionGate permission="key">` — conditionally renders children
- `<ProtectedRoute permission="key">` — redirects to `/403` if user lacks key

See `frontend/src/hooks/usePermission.ts`, `frontend/src/components/PermissionGate.tsx`,
and `frontend/src/components/ProtectedRoute.tsx`.

The frontend matrix test suite (`frontend/src/__tests__/permissionMatrix.test.tsx`)
verifies all major affordances are correctly gated per profile.

---

## Permission denial observability

When `require_permission()` denies access it emits:

```python
logger.warning(
    "Permission denied",
    extra={
        "event_name": AuthEvent.PERMISSION_DENIED,  # "auth.permission_denied"
        "outcome_code": OutcomeCode.PERMISSION_DENIED,
        "required_permission": key,
        "user_id": str(current_user.id),
        "profile": profile_name,
    },
)
```

See `docs/observability.md` → "Auth events" for the full taxonomy.

