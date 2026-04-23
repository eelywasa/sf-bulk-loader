# Auth & RBAC architecture

## What this covers / who should read this

How the app authenticates users, tracks identity lifecycle, and enforces role-based access. Read this before adding a permission-gated feature, debugging a login/invitation flow, or changing anything in `backend/app/auth/` or `frontend/src/context/AuthContext.tsx`.

For the authoritative permission-per-profile matrix, see [`docs/specs/rbac-permission-matrix.md`](../specs/rbac-permission-matrix.md) — do not duplicate it here.

---

## Auth modes

| Mode | Profile | Behaviour |
|---|---|---|
| `auth_mode=none` | `desktop` | No login. A virtual user (`id="desktop"`, all permissions) is injected on every request. |
| `auth_mode=local` | `self_hosted`, `aws_hosted` | Email + password login; JWT session; permissions enforced per profile. |

The mode is derived from `APP_DISTRIBUTION` but can be overridden via env. Desktop mode skips `seed_admin` and JWT validation entirely; see [`backend/app/services/auth.py`](../../backend/app/services/auth.py).

---

## Identity model

### User (`backend/app/models/user.py`)

Canonical fields:

- `email` — **sole identity column** (SFBL-198 removed the legacy `username` as identity). Unique, indexed.
- `hashed_password` — bcrypt over a SHA-256 prehash (see below). Nullable while status is `invited`.
- `display_name` — free-text UI label only. Never used for auth.
- `status` — enum replacing the legacy `is_active` boolean:
  - `invited` — awaiting password setup via invite link
  - `active` — normal login permitted (subject to `locked_until`)
  - `locked` — hard lock; admin unlock required
  - `deactivated` — explicitly disabled
  - `deleted` — soft-deleted; preserves audit trail
- `profile_id` (FK) — determines permissions; eager-loaded with the user on every authed request.
- `locked_until` — tier-1 temporary auto-lock set by progressive lockout; expires itself.
- `failed_login_count`, `last_failed_login_at` — lockout counters.
- `must_reset_password` — true for temp-password invitations; forces a reset on first login.
- `password_changed_at` — JWT watermark. Any token whose `iat < password_changed_at` is rejected even if the signature and expiry are valid — this invalidates sessions on password or email change.
- `invited_by` (self-FK, `ON DELETE SET NULL`) — who issued the invite; does not cascade.
- `invited_at`, `last_login_at` — audit timestamps.

### Profile (`backend/app/models/profile.py`)

Seeded at first boot with three rows: `admin`, `operator`, `viewer`. Each carries a frozenset of permission keys. A fourth virtual profile `desktop` exists only in memory for `auth_mode=none`.

### Bootstrap admin

On first boot (no users in DB), [`seed_admin()`](../../backend/app/services/auth.py) creates the initial admin from `ADMIN_EMAIL` + `ADMIN_PASSWORD`:

1. Validate password strength (≥12 chars, mixed case, digit, special).
2. Look up the seeded `admin` profile.
3. Insert the user row with `status='active'`, `profile_id=admin`, hashed password.
4. Also sets `profile_id` explicitly so a brand-new DB boots cleanly (SFBL-195).

If `ADMIN_EMAIL` / `ADMIN_PASSWORD` are unset on an empty DB, startup fails fast with guidance. Skipped entirely in `auth_mode=none`.

> **Legacy note.** `ADMIN_USERNAME` was the pre-SFBL-198 identity; it is no longer accepted. Any docs or deployment configs still mentioning it need updating.

---

## Password auth flow

### Login — `POST /api/auth/login`

Request: `{ "email": "...", "password": "..." }` → response: `{ "access_token": "...", "expires_in": N, "must_reset_password": bool }`.

Flow ([`backend/app/api/auth.py`](../../backend/app/api/auth.py)):

1. Per-IP rate limit (`LOGIN_RATE_LIMIT_ATTEMPTS`, `LOGIN_RATE_LIMIT_WINDOW_SECONDS`).
2. User lookup by email.
3. Status gate — reject anything other than `active`, and reject if `locked_until > now`.
4. Password verification.
5. On failure: `handle_failed_attempt()` — increments counters, sets `locked_until` per progressive policy.
6. On success: `handle_successful_login()` — clears counters, stamps `last_login_at`, mints JWT.
7. Persist a `LoginAttempt` row (IP, UA, outcome) for audit.

### Password hashing

bcrypt over a SHA-256 prehash, so passwords longer than 72 bytes are not truncated:

```python
bcrypt.hashpw(base64.b64encode(sha256(pw.encode()).digest()), bcrypt.gensalt())
```

### JWT

HS256 signed with `JWT_SECRET_KEY`. Claims: `sub` (user ID), `email`, `iat`, `exp`. No refresh tokens — clients re-authenticate on expiry. Lifetime is DB-backed (default 60 min; `jwt_expiry_minutes` setting).

The authenticated dependency [`get_current_user()`](../../backend/app/services/auth.py) gates on:

- valid signature and not expired
- user exists and `status='active'`
- `locked_until` not in the future
- `iat >= password_changed_at` (watermark kills old tokens after credential changes)

---

## RBAC

### Enforcement primitives

Backend (`backend/app/auth/permissions.py`):

```python
@router.get("/runs/{id}")
async def get_run(user: User = Depends(require_permission("runs.view"))): ...
```

`require_permission(key)` validates the key against `ALL_PERMISSION_KEYS` at factory time (typos fail at import), then at request time checks `key in current_user.profile.permission_keys`. Denials raise 403 with `detail={"required_permission": "<key>"}` and log `event_name="auth.permission_denied"` (taxonomy in [`docs/observability.md`](../observability.md)).

For router-level gating, chain it into the router:

```python
router = APIRouter(prefix="/api/runs", dependencies=[Depends(require_permission("runs.view"))])
```

Some endpoints require **multiple** permissions — e.g. `/api/jobs/{id}/success-csv` requires both `runs.view` (from the router) and `files.view_contents` (per-endpoint dependency). See SFBL-206.

### Frontend enforcement

Per the rule in [`docs/ui-conventions.md`](../ui-conventions.md) (§"Permission checks — routes vs elements"):

> Never use permission checks to hide navigation items from the URL bar (that is route-level enforcement via `ProtectedRoute`). Use `PermissionGate` and `usePermission` for in-page element visibility only.

- [`ProtectedRoute`](../../frontend/src/components/ProtectedRoute.tsx) — wraps a route; redirects unauthed → `/login`, permission-denied → `/403`.
- [`PermissionGate`](../../frontend/src/components/PermissionGate.tsx) — renders children only if a required key (or ALL / ANY set) is held; null otherwise.
- [`usePermission(key)`](../../frontend/src/hooks/usePermission.ts) — boolean hook for imperative checks.
- [`AuthContext`](../../frontend/src/context/AuthContext.tsx) — provides `authRequired`, `permissions` (a `Set<string>`), and the auth actions.

---

## Invitation flow

Tokens live in `InvitationToken` (`backend/app/models/invitation_token.py`). Only the **SHA-256 hash** of the raw token is stored; the raw value is delivered to the invitee via email and never persisted.

Fields: `token_hash`, `user_id`, `expires_at` (`now + INVITATION_TTL_HOURS`, default 24 h), `used_at`.

### Happy path

1. Admin issues the invite → user row created with `status='invited'`, token generated.
2. User clicks `{BASE_URL}/invite/accept?token=<raw>` → frontend calls `GET /api/invitations/{raw}` to validate and fetch invite metadata.
3. User sets a password → `POST /api/invitations/{raw}/accept`.
4. Backend validates strength, atomically redeems the token (single `UPDATE … WHERE token_hash=… AND used_at IS NULL AND expires_at > now`), flips the user to `status='active'`, stamps `password_changed_at` and `last_login_at`, returns a JWT.

Concurrent accept attempts race on the UPDATE — only one matches; the loser gets 410.

Expired or already-used tokens return 404 to avoid enumeration.

### Fallback when email is not configured

The admin receives a **temp password** in the API response (visible **once**, never retrievable). The user is created as `status='active'` with `must_reset_password=True`; on login the frontend redirects to a forced-reset screen.

---

## Admin bootstrap & break-glass

- `seed_admin()` runs in the FastAPI lifespan hook on every boot. If no users exist, it creates the admin; on an existing DB it is a no-op.
- For password recovery without email (locked out, no SMTP), a CLI recovery command is available — see [`docs/usage/admin-recovery.md`](../usage/admin-recovery.md) for the operator procedure.
- The last admin cannot be disabled, deactivated, or demoted (SFBL-188 safeguards).

---

## Testing the permission surface

- `backend/tests/test_permission_matrix.py` is the authoritative drift test — it hits every route as every profile and asserts 403 vs non-403. Any new permission-gated endpoint must add parametrised rows here.
- The matrix document `docs/specs/rbac-permission-matrix.md` and its YAML source `rbac-permission-matrix.yml` are enforced contracts; if the tests diverge from the YAML, the tests fail.
- Frontend has `frontend/src/__tests__/permissionMatrix.test.tsx` which asserts in-page gates behave correctly per profile.
