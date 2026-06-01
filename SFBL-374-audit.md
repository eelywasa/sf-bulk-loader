# SFBL-374 — RBAC Audit: `/api/input-connections` and peers

This document records the audit of all `backend/app/api/*.py` routers for
routes protected only by `Depends(get_current_user)` with no
`require_permission` call. It captures the gate/scope-out decision for each
finding.

---

## What was done in this ticket

`backend/app/api/input_connections.py` now mirrors the pattern in
`connections.py`:

| Route | Method | Permission |
|---|---|---|
| `/api/input-connections/` | GET (list) | `connections.view` |
| `/api/input-connections/` | POST (create) | `connections.manage` |
| `/api/input-connections/{id}` | GET (detail) | `connections.view` |
| `/api/input-connections/{id}` | PUT (update) | `connections.manage` |
| `/api/input-connections/{id}` | DELETE | `connections.manage` |
| `/api/input-connections/{id}/test` | POST | `connections.manage` |

Implementation: module-level `_require_view` / `_require_manage` singletons
(called once at import time, fail-fast on unknown key), router-level
`dependencies=[Depends(_require_view)]` for baseline authentication, and
per-route `Depends(_require_manage)` for mutating routes.

---

## Audit: other routers gated only by `get_current_user`

### `backend/app/api/me.py` — `/api/me`

Routes: `POST /api/me/password` (change password), `GET /api/me/login-history`.

**Decision: SCOPE OUT.**
These are self-service routes scoped to the authenticated user's own data. No
PAT would gain access to another user's data here. Adding a permission key
would only serve to accidentally lock users out of their own password-change
flow. Correct gate = authentication only. No RBAC gap.

---

### `backend/app/api/profile.py` — `/api/me`

Routes: `PUT /api/me` (update display name), `POST /api/me/email-change/request`,
`POST /api/me/email-change/confirm` (public, unauthenticated).

**Decision: SCOPE OUT.**
Same rationale as `me.py` — user can only mutate their own record. The
confirm endpoint is intentionally unauthenticated (token-gated). No RBAC gap.

---

### `backend/app/api/notification_subscriptions.py` — `/api/notification-subscriptions`

Routes: CRUD on per-user notification subscriptions.

**Decision: SCOPE OUT.**
The router already has a functional gate: `_block_desktop_profile()` is
called on every route (returns 403 on `auth_mode=none`), and each route
fetches `current_user` and enforces ownership — a user can only CRUD their
own subscriptions. A PAT authenticating as user X can only touch X's
subscriptions.

There is no existing `notifications.*` permission key, and adding one would
require a new key, a seed migration, and profile-matrix updates — well beyond
this ticket's scope (decision: no new keys). A follow-up ticket should
introduce `notifications.manage` if the product needs per-profile control.

---

### `backend/app/api/auth.py` — `/api/auth`

Includes: `POST /api/auth/login` (public), `GET /api/auth/me`
(authenticated), 2FA enrollment routes, password-reset flows.

**Decision: SCOPE OUT.**
Auth endpoints are infrastructure, not business data. Login/reset are
intentionally unauthenticated. `/api/auth/me` (session introspection) is
appropriately authentication-gated only — a PAT that can authenticate is
entitled to inspect its own session. No RBAC gap.

---

### `backend/app/api/admin_email.py` — `/api/admin/email`

Route: `POST /api/admin/email/test`.

**Decision: SCOPE OUT.**
The router uses a custom `require_admin` dependency that checks
`current_user.is_admin`. This predates the permission model. The router is
also not registered on the desktop profile (`auth_mode=none`). This is a
known pattern from SFBL-200/203; migrating it to `system.settings` is a
housekeeping item that belongs on a future admin-RBAC cleanup ticket, not
here.

---

### All other routers

All other routers in `backend/app/api/` are already gated by
`require_permission`:

| Router | Permission |
|---|---|
| `connections.py` | `connections.view` / `connections.manage` (+ `connections.view_credentials`) |
| `load_plans.py` | `plans.view` / `plans.manage` / `runs.execute` |
| `load_runs.py` | `runs.view` / `runs.execute` / `runs.abort` / `files.view_contents` |
| `load_steps.py` | (via load_plans router) |
| `jobs.py` | `runs.view` / `files.view_contents` |
| `utility.py` | `files.view` / `files.view_contents` (health/runtime/ws are intentionally open) |
| `settings.py` | `system.settings` |
| `admin_about.py` | `system.settings` |
| `admin_users.py` | `users.manage` |
| `invitations.py` | unauthenticated by design (token-gated) |
| `auth_2fa.py` | authentication-only by design |
| `auth_login_2fa.py` | authentication-only by design |
| `auth_reset.py` | token-gated (unauthenticated) |

---

## Summary

One gap closed by this ticket: `/api/input-connections` (6 routes, now gated
with `connections.view`/`connections.manage`).

Five other "authentication-only" routers found (`me`, `profile`,
`notification_subscriptions`, `auth`, `admin_email`). All are explicitly
scoped out with rationale above — none represent a PAT-exploitable privilege
escalation to another user's data or to system-level operations, with the
partial exception of `admin_email` (guarded by `is_admin` check, not a PAT
bypass).
