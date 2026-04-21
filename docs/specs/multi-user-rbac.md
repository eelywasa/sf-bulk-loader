# Multi-User Support & RBAC — Specification

**Spike:** SFBL-185  
**Status:** Draft v2 — review feedback addressed, ready for epic breakdown  
**Last updated:** 2026-04-20

---

## 1. Background & Motivation

sf-bulk-loader currently supports a **single admin account** bootstrapped from environment variables (`ADMIN_USERNAME` / `ADMIN_PASSWORD`). As teams grow, operators need:

1. **Multiple named users** — so that audit trails identify individuals, not a shared account.
2. **User management** — admins invite/provision new users without touching the server environment.
3. **Role-based access control** — limit what non-admin users can see and do.
4. **Defence in depth** — login attempt tracking, lockout protection, structured auth observability.

### Delivery ordering — note

An earlier draft framed delivery as *"Phase 1 = multi-user, all admin; Phase 2 = RBAC"*. That framing has been **retired**. The multi-user API and UI require a profile selector from day one, so shipping "all admin" first would create data-migration debt when RBAC lands.

Actual delivery order (see §12):

1. **Epic A — Security & Observability Uplift.** Standalone. Closes gaps in the current login endpoint (no logging, no lockout). Does not depend on RBAC.
2. **Epic B — Permission Model Foundation.** Introduces profiles (`admin`, `operator`, `viewer`), permission keys, backend enforcement, frontend gating. The seeded admin continues to have all permissions; no user-visible role change on its own.
3. **Epic C — Multi-User Invitations.** Admins invite new users and choose their profile from the seed list. Because Epic B has landed, invitations can target any profile from day one.

---

## 2. Current State

| Aspect | Current behaviour |
|---|---|
| Users | Single admin; `users` table exists with `role` column (`admin` / `user`) |
| Auth | JWT Bearer, HS256, 60-min expiry |
| Bootstrap | `ADMIN_USERNAME` + `ADMIN_PASSWORD` env vars seed first admin on startup |
| Roles | `role` column present but not enforced anywhere |
| Desktop mode | `auth_mode=none` — no auth at all |
| Login observability | **None** — login endpoint has no structured logging, no metrics, no IP capture |
| Login rate-limit / lockout | **None** — unlimited login attempts, no account lockout |

---

## 3. Resolved Design Decisions

| # | Question | Decision |
|---|---|---|
| Q1 | Invite flow | Email invite when email service configured; **temp-password fallback** when not. Temp-password users must reset on first login. |
| Q2 | User deletion | **Soft delete** via `status='deleted'` tombstone. Perpetual traceability required. |
| Q3 | Invite token TTL | **24 hours**, configurable via `INVITATION_TTL_HOURS` |
| Q4 | Self-registration | **No.** Admin-provisioned only. No public signup page or API. |
| Q5 | Role taxonomy + PII | Three profiles: **admin, operator, viewer**. Separate `files.view_contents` permission — viewer cannot preview/download file contents. |
| Q6 | Global vs. resource-scoped | **Global** roles. Multi-environment separation (prod vs. non-prod) handled by deploying separate instances. |
| Q7 | Operator sees Connection credentials? | **No.** Operator sees connection *name* only. Editing connections is admin-only. |
| Q8 | Permission model | **Granular permission keys + fixed profiles.** Permissions are first-class (`connections.manage`, `plans.edit`, etc.); profiles are seeded bundles. Custom profiles deferred to a future epic; no schema change needed to add them later. Analogous to a Salesforce permission-set model with profile-only UX for now. |
| Q9 | Login activity tracking | **Yes, extended.** Per-attempt login log (IP, UA, outcome), progressive lockout, full auth observability. See §8. |
| Q10 | Invite when email unavailable | **Temp-password fallback.** Admin sees the temporary password once; user must reset on first login. |
| Q11 | Identity: email vs. username | **Email is the sole login identifier.** No username in the auth path. `display_name` covers UI labels. `username` column is dropped (or retained as a deprecated nullable for the bootstrapped admin — see §9). |
| Q12 | User state model | **`status` enum** replaces `is_active`: `invited`, `active`, `locked`, `deactivated`, `deleted`. Tier-1 temp lockout is orthogonal — uses `locked_until` without changing `status`. Each admin action maps to a specific transition; unlock cannot accidentally revive a deliberately deactivated user. |

---

## 4. Multi-User Invitations (Epic C)

### 4.1 Identity Model

- **Login identifier:** email address. `/api/auth/login` accepts `{email, password}`. No username field.
- **`display_name`:** free-text label shown in the UI.
- **Bootstrapped admin compatibility:** see §9 (Migration Strategy) for handling the existing `ADMIN_USERNAME` → `ADMIN_EMAIL` transition.
- **Uniqueness:** email is unique across non-deleted rows. A deleted user (status=`deleted`) does not block a new invite to the same address, but historical `login_attempts` rows preserve the original `user_id` FK for traceability.

### 4.2 Invitation Flow

**When email is configured:**
1. Admin POSTs `/api/admin/users` with `{email, display_name, profile_id}`.
2. Backend creates `User` row (`status='invited'`, no password hash), creates `InvitationToken`.
3. Email sent with link `{APP_BASE_URL}/accept-invite/{raw_token}`.
4. User visits link → frontend calls `POST /api/auth/accept-invite` with `{token, password}`.
5. Backend validates token, sets password hash, transitions user to `status='active'`, marks token used.
6. User redirected to login.

**When email is not configured (temp-password fallback):**
1. Admin POSTs `/api/admin/users` with `{email, display_name, profile_id}`.
2. Backend creates `User` row (`status='active'`, `must_reset_password=true`), generates a secure 16-char temp password.
3. POST response returns the temp password **once** (never retrievable after).
4. Admin communicates credentials out-of-band.
5. User logs in with email + temp password; `must_reset_password=true` redirects to a forced-reset screen.
6. After reset, `must_reset_password=false`, full access granted.

### 4.3 User Management API

All endpoints require `users.manage` permission (admin only).

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/admin/users` | List users (id, email, display_name, profile, status, last_login_at, created_at). Supports filter by status. |
| `POST` | `/api/admin/users` | Invite / create user. Returns invite status or temp password. |
| `GET` | `/api/admin/users/{id}` | User detail incl. recent login history |
| `PUT` | `/api/admin/users/{id}` | Update `profile_id`, `display_name` |
| `POST` | `/api/admin/users/{id}/reset-password` | Trigger password reset (email or return temp password) |
| `POST` | `/api/admin/users/{id}/deactivate` | Transition `active → deactivated` |
| `POST` | `/api/admin/users/{id}/reactivate` | Transition `deactivated → active` |
| `POST` | `/api/admin/users/{id}/unlock` | Transition `locked → active` (tier-2 recovery); also clears `locked_until` for tier-1 |
| `DELETE` | `/api/admin/users/{id}` | Soft delete → `status='deleted'` (tombstone) |
| `POST` | `/api/admin/users/{id}/resend-invite` | Issue a new invitation token |

Each endpoint performs an explicit status transition; invalid transitions return 409 Conflict with the current status in the error body.

### 4.4 Invite Token Model

```
invitation_tokens
  id            UUID PK
  user_id       FK → users.id
  token_hash    TEXT (SHA-256 of raw token)
  created_at    TIMESTAMP
  expires_at    TIMESTAMP  (TTL: 24h, configurable)
  used_at       TIMESTAMP nullable
```

Single-use token, raw value never stored. Same pattern as `password_reset_tokens` and `email_change_tokens`.

### 4.5 Preventing Last-Admin Lockout

Enforced at API layer with explicit error responses:
- Cannot deactivate your own account.
- Cannot change your own profile away from admin.
- Cannot demote the last admin (status=`active`, profile=`admin`) to a non-admin profile.
- Cannot soft-delete the last admin.

### 4.6 Admin UI — User Management

New route: `/admin/users` (only visible to admins).

Components:
- User table: email, display name, profile badge, status badge, last login, actions.
- Status filter chips: `active`, `invited`, `locked`, `deactivated`. Deleted users hidden by default (opt-in "show deleted").
- "Invite User" button → modal with email + display_name + profile selector.
- "Invite sent" or "Temp password generated" confirmation showing temp password (copy-to-clipboard, dismisses permanently).
- Per-row actions: edit profile, deactivate/reactivate, reset password, resend invite, unlock (if locked), view history.
- User detail page: recent sign-in activity (last 25 attempts).

Sidebar gains an "Admin" section visible only to users with `users.manage`.

---

## 5. Permission Model Foundation (Epic B)

### 5.1 Permission Keys

First-class permission strings. Initial set:

| Key | Description |
|---|---|
| `connections.view` | List connections + see connection names |
| `connections.view_credentials` | See host, consumer key, key metadata |
| `connections.manage` | Create / edit / delete connections |
| `plans.view` | View load plans and steps |
| `plans.manage` | Create / edit / delete load plans |
| `runs.view` | View load runs and job summaries |
| `runs.execute` | Trigger runs |
| `runs.abort` | Abort an in-flight run |
| `files.view` | Browse files list + metadata |
| `files.view_contents` | Preview / download raw file contents |
| `users.manage` | Full user management (invite, edit, deactivate) |
| `system.settings` | View / change system settings |

### 5.2 Seeded Profiles

| Permission | admin | operator | viewer |
|---|:---:|:---:|:---:|
| `connections.view` | ✓ | ✓ (name only) | ✓ (name only) |
| `connections.view_credentials` | ✓ | ✗ | ✗ |
| `connections.manage` | ✓ | ✗ | ✗ |
| `plans.view` | ✓ | ✓ | ✓ |
| `plans.manage` | ✓ | ✓ | ✗ |
| `runs.view` | ✓ | ✓ | ✓ |
| `runs.execute` | ✓ | ✓ | ✗ |
| `runs.abort` | ✓ | ✓ | ✗ |
| `files.view` | ✓ | ✓ | ✓ |
| `files.view_contents` | ✓ | ✓ | ✗ |
| `users.manage` | ✓ | ✗ | ✗ |
| `system.settings` | ✓ | ✗ | ✗ |

Note: operator *can* manage load plans (including editing) — the rationale for trigger-only was rejected here since plans-as-code are typically the operator's job.

**Viewer:** view-only, no file contents, no run execution.  
**Operator:** runs the show day-to-day, cannot invite users or change connection credentials.  
**Admin:** everything.

### 5.3 Data Model — Profiles

```
profiles
  id                SERIAL PK
  name              TEXT UNIQUE    ('admin', 'operator', 'viewer')
  description       TEXT
  is_system         BOOLEAN        (true for seeded profiles — not editable/deletable)
  created_at        TIMESTAMP

profile_permissions
  profile_id        FK → profiles.id
  permission_key    TEXT
  PRIMARY KEY (profile_id, permission_key)
```

`users.role` column replaced by `users.profile_id` FK → `profiles.id`. Alembic migration: backfill existing `role='admin'` → admin profile, `role='user'` → viewer profile (safe default).

### 5.4 Enforcement

**Backend:**
- New dependency factory: `require_permission("permission.key")`.
- Replaces or supplements `get_current_user` on protected routes.
- Permission check on cached `user.profile.permissions` set.
- Default-deny on unknown keys.

**Frontend:**
- `AuthContext` exposes `user.permissions: Set<string>` on login/refresh.
- `usePermission(key)` hook returns `boolean`.
- `<PermissionGate permission="key">...</PermissionGate>` wrapper for conditional UI.
- Protected route component honours permission requirements and redirects to `/403` for forbidden access (distinct from `/login` for unauthenticated).

### 5.5 Desktop Mode

Unchanged. `_DESKTOP_USER` gets a virtual profile with *all* permissions. No enforcement cost in desktop mode.

---

## 6. Data Model Changes — Summary

### 6.1 `users` table changes

**Columns added:**

| Column | Type | Purpose |
|---|---|---|
| `profile_id` | FK → profiles.id NOT NULL | Replaces `role` |
| `status` | TEXT NOT NULL | Enum: `invited`, `active`, `locked`, `deactivated`, `deleted`. Replaces `is_active`. |
| `invited_by` | FK → users.id nullable | Who invited this user |
| `invited_at` | TIMESTAMP nullable | When invite was issued |
| `last_login_at` | TIMESTAMP nullable | Most recent successful login |
| `must_reset_password` | BOOLEAN default false | Forces password change on next login |
| `locked_until` | TIMESTAMP nullable | Tier-1 auto-unlock deadline (orthogonal to `status`) |
| `failed_login_count` | INT default 0 | Rolling counter since last success |
| `last_failed_login_at` | TIMESTAMP nullable | Anchor for sliding-window lockout check |

**Columns dropped:**

| Column | Replaced by |
|---|---|
| `role` | `profile_id` |
| `is_active` | `status` |
| `username` | `email` (login identifier) |

**Auth gate (new canonical check in `get_current_user`):**
```
status == 'active' AND (locked_until IS NULL OR locked_until <= now)
```

Status transitions — explicit, each driven by a distinct admin action or system event:

```
invited   → active        (accept invite)
active    → locked        (tier-2 lockout)
active    → deactivated   (admin deactivate)
locked    → active        (admin unlock)
deactivated → active      (admin reactivate)
any       → deleted       (admin soft-delete; terminal)
```

### 6.2 New tables

- `profiles` (§5.3)
- `profile_permissions` (§5.3)
- `invitation_tokens` (§4.4)
- `login_attempts` — per-attempt audit log (§8.2)

### 6.3 Alembic migration order

1. **Epic B:**
   1. `create_profiles_and_profile_permissions` — seed admin / operator / viewer with permission keys
   2. `users_migrate_role_to_profile_id` — add `profile_id`, backfill (`role='admin'` → admin, `role='user'` → viewer), drop `role`
2. **Epic A:**
   3. `create_login_attempts`
   4. `users_add_status_and_lockout_columns` — add `status`, `locked_until`, `failed_login_count`, `last_failed_login_at`, `must_reset_password`. Backfill `status` from old `is_active` (`true → 'active'`, `false → 'deactivated'`), then drop `is_active`.
3. **Epic C:**
   5. `create_invitation_tokens`
   6. `users_add_invitation_columns` — `invited_by`, `invited_at`, `last_login_at`, plus migration of `ADMIN_USERNAME` → email (§9)

---

## 7. Email Service Dependency

Phase 1 depends on the email service from SFBL-136. Behaviour when email is not configured:
- Invite flow falls back to temp-password (§4.1).
- Password reset (admin-initiated) falls back to temp-password.
- Account-lockout notifications: suppressed; admin is expected to notice via the user list.
- Admin UI shows a small badge in Settings indicating email transport is off.

---

## 8. Security Uplift & Observability

### 8.1 Progressive Lockout

Two tiers, both tuneable via env vars:

| Tier | Threshold | Action | Recovery |
|---|---|---|---|
| 1 | 5 failed logins on same username in 15 min | `locked_until = now + 30 min` | Auto-unlock at `locked_until` |
| 2 | 10 cumulative failures since last success, or 3 tier-1 locks in 24h | `is_active=false` + email to user | Admin must unlock via `/api/admin/users/{id}/unlock` |

Config keys (with defaults):
- `LOGIN_TIER1_THRESHOLD=5`
- `LOGIN_TIER1_WINDOW_MINUTES=15`
- `LOGIN_TIER1_LOCK_MINUTES=30`
- `LOGIN_TIER2_THRESHOLD=10`
- `LOGIN_TIER2_TIER1_COUNT=3`
- `LOGIN_TIER2_WINDOW_HOURS=24`

On successful login: reset `failed_login_count=0`, clear `locked_until`.

### 8.2 Login Attempt Log

```
login_attempts
  id            SERIAL PK
  user_id       FK → users.id nullable   (null if username unknown)
  username      TEXT                     (as submitted; for unknown-user tracking)
  ip            TEXT
  user_agent    TEXT
  outcome       TEXT                     (see outcome codes §8.4)
  attempted_at  TIMESTAMP
```

Retention: **90 days** (configurable via `LOGIN_ATTEMPT_RETENTION_DAYS`). Nightly cleanup task.

### 8.3 Per-IP Rate Limiting

Uses existing `services/rate_limit.py`:
- 20 attempts per IP per 5 minutes across *any* username.
- Returns 429 on breach; emits `auth.login.rate_limited` with outcome `ip_limit`.

**Known limitation — per-process counter.** `rate_limit.py` is in-memory and per-process. The current production deployment runs a single uvicorn worker, so the limit is accurate. For multi-worker or multi-container deployments, the effective limit multiplies by worker count — acceptable in the short term but flagged as a follow-up. A shared-store implementation (SQLite-backed counter or Redis) is listed in §11 (Out of Scope / Deferred) for a later ticket. The spec explicitly does not widen the rate-limit scope in this epic.

### 8.4 Observability — New Events

Add to `AuthEvent` in `observability/events.py`. Event values use the existing dot-namespaced lowercase convention (e.g. `auth.password.changed`). Outcome codes reuse existing entries in `OutcomeCode` where possible (`success`, `invalid_token`, `expired_token`, `used_token`) and add new lowercase snake_case entries otherwise.

| Event constant | Event value | Outcome codes |
|---|---|---|
| `LOGIN_SUCCEEDED` | `auth.login.succeeded` | `success` |
| `LOGIN_FAILED` | `auth.login.failed` | `wrong_password`, `unknown_user`, `user_inactive` (existing), `user_locked`, `must_reset_password` |
| `LOGIN_RATE_LIMITED` | `auth.login.rate_limited` | `ip_limit` |
| `ACCOUNT_LOCKED` | `auth.account.locked` | `tier1_auto`, `tier2_hard` |
| `ACCOUNT_UNLOCKED` | `auth.account.unlocked` | `tier1_auto_expired`, `admin_manual` |
| `ADMIN_RECOVERED` | `auth.admin.recovered` | `cli_recovery` |
| `USER_INVITED` | `auth.user.invited` | `email_sent`, `temp_password_generated` |
| `INVITATION_ACCEPTED` | `auth.invitation.accepted` | `success`, `expired_token`, `invalid_token`, `used_token` |
| `USER_PROFILE_CHANGED` | `auth.user.profile_changed` | `success` |
| `USER_DEACTIVATED` | `auth.user.deactivated` | `by_admin`, `tier2_lockout` |
| `USER_REACTIVATED` | `auth.user.reactivated` | `by_admin` |
| `USER_DELETED` | `auth.user.deleted` | `by_admin` |

New outcome-code additions to `OutcomeCode`: `wrong_password`, `unknown_user`, `user_locked`, `must_reset_password`, `ip_limit`, `tier1_auto`, `tier2_hard`, `tier1_auto_expired`, `admin_manual`, `cli_recovery`, `email_sent`, `temp_password_generated`, `by_admin`, `tier2_lockout`.

Docstring entries must be added to the `OutcomeCode` docstring alongside the existing auth codes (SFBL-145 – SFBL-148 section), per the observability runbook.

### 8.5 Observability — New Metrics

Add to `observability/metrics.py`:
- `auth_login_attempts_total{outcome}`
- `auth_account_locks_total{tier}`
- `auth_account_unlocks_total{method}`
- `auth_admin_recoveries_total` (CLI break-glass; no label)
- `auth_invitations_total{outcome}`
- `auth_invitation_acceptances_total{outcome}`

### 8.6 User-Facing Surfaces

**Profile page gains "Recent sign-in activity"**
- Shows last 10 attempts (own user only): timestamp, IP, outcome (masked to "Success" / "Failed").
- Helps users spot suspicious activity.

**Email notifications sent to user:**
- Password changed successfully (existing behaviour — keep).
- Account locked (tier 2): "Your account was locked due to repeated failed sign-in attempts. Contact your administrator."
- Optional: new login from new IP — deferred, tracked as a follow-up.

**Admin user detail page:**
- Full login history table with filters.
- Unlock button (visible when `locked_until` set or `is_active=false` after tier-2 lock).

---

## 9. Migration Strategy — Existing Single-Admin Installs

Existing installs have one admin user bootstrapped from env vars (`ADMIN_USERNAME`, `ADMIN_PASSWORD`). On upgrade:

1. Migrations run automatically (Alembic, in the order listed in §6.3).
2. `profiles` table seeded with admin / operator / viewer.
3. Existing user row: `role='admin'` → `profile_id = admin.id`; `role='user'` → `profile_id = viewer.id`. `role` column dropped.
4. `is_active=true` → `status='active'`; `is_active=false` → `status='deactivated'`. `is_active` column dropped.
5. `users.manage` permission is on the admin profile — the existing admin can immediately invite new users.

### 9.1 Username → Email Transition

Auth moves to email-based login (Q11). Two concerns for existing installs:

**Bootstrap env vars.** A new `ADMIN_EMAIL` env var is required alongside `ADMIN_USERNAME` / `ADMIN_PASSWORD` for first-boot seed. On upgrade of an existing install:
- If `ADMIN_EMAIL` is set **and** the existing admin's `users.email` is null or blank → backfill `users.email = ADMIN_EMAIL` during migration.
- If `ADMIN_EMAIL` is **not set** and the existing admin has no email → startup fails with a clear error pointing at the migration documentation. The operator must set `ADMIN_EMAIL` and restart.
- If the existing admin already has an email (because they set it via SFBL-148 email change) → no action needed; `ADMIN_EMAIL` is ignored on subsequent boots (only used at first seed).

**Username column.** The `username` column is dropped after the email backfill confirms all active/invited/locked/deactivated users have a non-null email. Migration verifies this invariant and aborts if any user lacks an email.

**Login API change.** `/api/auth/login` switches from `{username, password}` to `{email, password}`. This is a breaking API change. Because login tokens are session-only (JWTs, client-held), no migration is needed beyond updating the frontend login form, which ships in the same PR.

### 9.2 Zero-downtime considerations

All migrations are backward-compatible within a single deployment step (each migration script wraps rename/drop operations in a single transaction). There is no intermediate deployed state where half the migrations have run — the application can only boot against a fully-migrated schema.

---

## 10. Admin Recovery / Anti-Bricking

Single-admin installs carry a real bricking risk: forgotten password + no email, hard lockout with no second admin to unlock, or email-based account takeover. Two defences:

### 10.1 Break-glass CLI command

Server-side recovery command, invokable only with filesystem / shell access to the backend container:

```
python -m app.cli admin-recover <username>
```

Behaviour:
- Generates a secure 16-char temporary password and prints it to stdout once.
- Clears `locked_until`, resets `failed_login_count`, sets `is_active=true`, sets `must_reset_password=true`.
- Refuses to operate on users whose profile is not `admin` (recovery is for admins only).
- Emits a high-visibility `ADMIN_RECOVERED` audit event with the invoking host's username (from `os.getlogin()`), stored in `login_attempts` with `outcome='CLI_RECOVERY'`.
- Logs a warning line to the server log.

The security boundary is filesystem access to the backend. Anyone with shell access to the container already owns the app — this just gives them a safe, audited recovery path instead of hand-editing the database.

Related commands under the same CLI entrypoint:
- `python -m app.cli list-admins` — enumerate active admin users
- `python -m app.cli unlock <username>` — clear lockout without resetting password

### 10.2 UI nudge: single-admin warning

When the count of active admin users is exactly 1, the admin user management page (`/admin/users`) displays a persistent (non-dismissible) banner:

> ⚠ **Only one admin configured** — promote a second user to admin to avoid account lockout risk. See [Admin recovery](#) for what to do if you get locked out.

Shown on every visit. The linked help page explains both the CLI recovery procedure and the recommendation to maintain at least two admins.

The banner disappears automatically when a second admin is created.

---

## 11. Out of Scope / Deferred

- **SAML SSO** — `saml_name_id` column stays for future use.
- **OAuth / social login.**
- **Custom profile editor UI** — data model supports it; UI deferred to a future epic once user feedback is in.
- **Resource-level permissions** — global roles only for now.
- **Field-level PII masking** — file contents gated as a single permission; per-field redaction deferred.
- **New-IP login notifications** — deferred as a follow-up.
- **User groups / teams.**
- **Multi-environment (prod vs. non-prod) within a single instance** — handled by deploying separate instances.
- **Multi-worker / multi-container rate limiting** — current `services/rate_limit.py` is per-process (§8.3). Shared-store implementation (SQLite counter or Redis) deferred to a follow-up ticket when multi-worker deployment becomes a concrete requirement.
- **Username re-introduction** — if product feedback later calls for username-based login, it re-opens Q11; not planned.

---

## 12. Proposed Epic Breakdown

### Epic A — Security & Observability Uplift *(deliver first — fixes existing gaps)*
- `users` table: `status` enum column + `locked_until` + `failed_login_count` + `last_failed_login_at` + `must_reset_password` (migrations per §6.3)
- Login attempt log model + migration
- Login endpoint: structured logging (IP + UA + user agent capture), event emission per §8.4, metrics per §8.5
- Per-IP rate limiting on login (with known per-process limitation noted in code + runbook)
- Progressive lockout logic (tier 1 temp lock via `locked_until`, tier 2 hard lock via `status='locked'`)
- Unlock endpoint + admin UI control
- "Recent sign-in activity" on profile page
- Account-locked email notification
- Break-glass CLI (`python -m app.cli admin-recover` / `unlock` / `list-admins`)
- `OutcomeCode` docstring updated with new auth codes

### Epic B — Permission Model Foundation
- `profiles` + `profile_permissions` tables + seed data
- Migration: `users.role` → `users.profile_id`
- Backend `require_permission()` dependency
- Frontend `usePermission` hook + `<PermissionGate>` + `/403` page
- Apply guards to all existing endpoints and UI components
- Integration tests per profile × resource matrix

### Epic C — Multi-User Invitations
- `invitation_tokens` model + migration
- `users` table columns: `invited_by`, `invited_at`, `last_login_at`
- Username → email migration (§9.1): `ADMIN_EMAIL` env var, drop `username`, switch login API
- Admin user management API (`/api/admin/users/*`) with explicit status transitions
- Admin user management UI (`/admin/users`)
- Email invite flow (depends on SFBL-136 email service)
- Temp-password fallback flow
- Accept-invite frontend page
- Force-reset-on-first-login flow (uses `must_reset_password` from Epic A)
- Last-admin-lockout guards (via `status` + `profile_id` checks)
- Single-admin UI warning banner on `/admin/users`

Each epic ships as a single PR per the repo's epic delivery rule.
