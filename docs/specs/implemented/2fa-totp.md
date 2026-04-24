> **Archived — SFBL-244 (2026-04-24).** This document captured the design at delivery. For current behaviour see [`docs/usage/two-factor-auth.md`](../../usage/two-factor-auth.md) and the auth sections of [`docs/architecture.md`](../../architecture.md) and [`docs/observability.md`](../../observability.md). No further edits — historical reference only.

---

# TOTP 2FA — Implementation Spec

Status: implemented (SFBL-244)
Epic: SFBL-244
Author: spec only — no code in this document
Related: `docs/specs/rbac-permission-matrix.md`, existing lockout work (SFBL-191), settings service (SFBL-153)

---

## 0. Locked decisions

Inputs to this spec — not to be re-litigated during implementation.

- **D1** Enforcement is tenant-wide. Source: `REQUIRE_2FA` env var (startup default) AND a UI-togglable DB override. DB value wins when set.
- **D2** Applies only where `auth_mode != 'none'` (self_hosted, aws_hosted). Desktop profile is exempt.
- **D3** Recovery = backup codes only (10 one-time codes, bcrypt-hashed at rest, consumed on use). Admin reset clears TOTP + backup codes.
- **D4** 2FA challenge on **every** login. No "remember this device" cookie.
- **D5** Passkeys / WebAuthn are out of scope (noted as future enhancement in §13).
- **D6** Failed TOTP / backup-code attempts count toward the **same** progressive-lockout counters as failed passwords (`services/auth_lockout.py`). Prevents a bypass where an attacker who has the password burns unlimited 6-digit guesses.
- **D7** Toggling `require_2fa` on does **not** invalidate existing sessions. Users without a factor are forced to enrol at next login; currently-active sessions remain valid until natural expiry.
- **D8** When `require_2fa` is on, users cannot self-disable their own 2FA (including the sole admin). Break-glass CLI (SFBL-193) is the documented recovery path if the authenticator is lost.
- **D9** Backup code count = 10.
- **D10** TOTP parameters: SHA1 / 6 digits / 30s period / ±1 step window. Chosen for maximum authenticator-app compatibility (RFC 6238 standard).
- **D11** Forced-enrolment abandonment is **stateless**: no `user_totp` row is written until the user successfully confirms the first code. If the user closes the tab mid-wizard, nothing is persisted. On return they log in again, receive a fresh secret, and re-scan. The pre-auth `mfa_token` TTL (5 min) bounds the window.

---

## 1. Goal and non-goals

### Goal

Add TOTP-based second-factor authentication (RFC 6238) to the SFBL hosted
profiles. Tenant-wide enforcement, toggled at runtime by an admin, with
backup codes for recovery.

### In scope

- Per-user TOTP enrollment (generate secret, scan QR, confirm first code).
- Login challenge — two-phase flow, TOTP or backup code, on every login.
- Backup codes (8–10 single-use codes) shown once at enrollment and on regen.
- Admin reset of a user's 2FA.
- Tenant-wide `require_2fa` setting: env default + DB-backed override.
- Full observability (events + metrics) and docs updates.

### Non-goals (future enhancements)

- **WebAuthn / passkeys** — explicitly deferred. Listed in §13.
- **SMS / email OTP** — phishable; not added.
- **Remember-this-device cookie** — out of scope per locked decision D4.
- **Per-profile enforcement** (e.g. enforce only for admin profile). Tenant-wide only.
- **Step-up auth** for sensitive actions mid-session. Login gate only.
- **Desktop profile 2FA** — `auth_mode == 'none'` is exempt per decision D2.

---

## 2. User-visible flows

### 2.1 Enrollment (self-service)

Trigger: user navigates to their profile / security page and clicks "Set up
two-factor authentication", **or** is forced into enrollment at login when
tenant `require_2fa` is on and they have no factor enrolled yet (see §2.3).

1. Frontend calls `POST /api/auth/2fa/enroll/start` — backend generates a
   candidate secret **in memory only** (no DB write) and returns
   `{ secret_base32, otpauth_uri, qr_svg }`. Per D11, no `user_totp` row is
   created at this step.
2. UI shows the QR (inline SVG from the backend) and the base32 secret for
   manual entry. Issuer name = "SFBL" + tenant `base_url` host; account label
   = user's email. The client holds the `secret_base32` for the duration of
   the wizard.
3. User enters a 6-digit code from their authenticator.
4. Frontend calls `POST /api/auth/2fa/enroll/confirm` with
   `{ secret_base32, code }`.
5. Backend verifies the code against the supplied secret (±1 step window).
   On success: insert the `user_totp` row (secret Fernet-encrypted),
   generate and persist the backup-code set, bump
   `user.password_changed_at` (invalidates any existing JWTs — SFBL-145
   watermark; prevents concurrent sessions on other devices from
   continuing without the new factor), issue a **fresh access token**
   whose `iat` post-dates the bumped `password_changed_at`, and return
   `{ access_token, expires_in, backup_codes: [...] }` **once**. The
   frontend must replace its stored token with the new `access_token`;
   the caller's previous token is already invalid at this point.
6. UI shows the backup codes with "download" and "copy" affordances and an
   explicit "I've saved these" checkbox before dismissal.

Abandonment (per D11): if the user closes the tab between steps 1 and 4,
nothing is persisted. On return they log in again and start a fresh
`/enroll/start` — a new secret is generated. If they had already scanned
the prior QR into their authenticator, that entry becomes a stale /
unused credential; the user can delete it from their app. This is
considered an acceptable rare-case papercut in exchange for no DB state
to sweep.

### 2.2 Login challenge (happy path)

1. User POSTs `/api/auth/login` with email + password.
2. Backend verifies password. Branch precedence (evaluated top-down):
   - **(a)** User has a `user_totp` row → respond
     `{ mfa_required: true, mfa_token, mfa_methods: ["totp", "backup_code"], must_enroll: false }`.
   - **(b)** No `user_totp` row AND tenant `require_2fa` is on → respond
     `{ mfa_required: true, mfa_token, mfa_methods: ["enroll"], must_enroll: true }` (§2.3).
   - **(c)** Otherwise → return the full `TokenResponse` as today.
   Note: `require_2fa` being on does NOT force an enrolled user down the
   enroll branch — (a) wins because they already have a factor.
3. UI prompts for a 6-digit code.
4. User POSTs `/api/auth/login/2fa` with `{ mfa_token, code, method }`.
5. Backend validates `mfa_token` (claim `mfa_pending=true`, `sub=<user_id>`,
   TTL 5 min, signed with same JWT secret), then validates the TOTP code.
6. On success: returns the normal `TokenResponse` (full-access JWT,
   `must_reset_password`, etc). This is the point at which "login
   succeeded" side effects fire (see §2.4).

### 2.3 Forced-enrollment at login

When branch (b) above fires, the frontend routes the user to an
enrollment screen. This flow uses a **dedicated pair of pre-auth
endpoints**, because `mfa_token` is rejected by `get_current_user` and
the self-service enrollment routes at `/api/auth/2fa/*` require a
fully-authenticated session.

1. Frontend calls `POST /api/auth/login/2fa/enroll/start` with body
   `{ mfa_token }`. This endpoint is gated by `get_mfa_pending_user`
   and additionally requires `must_enroll=true` on the claim. It
   returns `{ secret_base32, otpauth_uri, qr_svg }` (in-memory generation
   only, no DB row written — per D11).
2. UI shows the QR; user scans and enters the 6-digit confirmation.
3. Frontend POSTs `/api/auth/login/2fa/enroll-and-verify` with
   `{ mfa_token, secret_base32, code }`. Backend verifies the code
   against the supplied secret, atomically inserts `user_totp` + 10
   `user_backup_code` rows, applies the "login succeeded" side effects
   (§2.4), and returns the full-access `TokenResponse` together with
   `{ backup_codes: [...] }` shown once.

The self-service routes under `/api/auth/2fa/enroll/*` (§4.1) remain
distinct and continue to require `get_current_user` — they are only
reachable by users who already have an active session.

Per D11, abandoning this wizard mid-flow persists nothing. The user's
`mfa_token` expires after 5 minutes; on return they log in again and
start a fresh enrolment. If they had already scanned the earlier QR,
that authenticator entry becomes orphaned and the user can delete it.

### 2.4 Login success side effects — phase 1 vs phase 2

`POST /api/auth/login` is **pre-authentication only** when the response
is `mfa_required: true`. It must NOT perform any of the side effects
today associated with "successful login" in
`backend/app/api/auth.py:286` — specifically:

- No `login_attempt` row with `outcome="succeeded"`.
- No `auth_lockout.handle_successful_login()` call (counters are NOT
  reset yet).
- No `auth.login.succeeded` event.
- No `last_login_at` bump on the user row.
- No `must_reset_password` handling (deferred to phase 2).

The only phase-1 side effects on a password-correct request that
triggers MFA:

- A new `login_attempt` row with `outcome="mfa_challenge_issued"`.
- A new event `auth.login.mfa_challenge_issued`.
- The `mfa_token` is issued.
- Progressive lockout counters are **not** decremented/reset — they
  remain at whatever state the password check left them in, so a
  correct password immediately followed by an exhausted MFA window
  cannot by itself clear prior failed-attempt state.

Phase 2 (`/login/2fa`, backup-code variant, and
`/login/2fa/enroll-and-verify`) is the point at which full "login
succeeded" semantics fire: `handle_successful_login()` (resets
counters), `login_attempt` success row, `auth.login.succeeded` event,
`last_login_at` update, `must_reset_password` enforcement. Phase-2
failures call `handle_failed_attempt()` with
`outcome="wrong_mfa"` / `"backup_code_used"` etc. per §10.5.

This split is normative — an implementation that fires success events
in phase 1 is a spec violation.

### 2.5 Backup-code login

Same as §2.2 but the UI offers a "use a backup code instead" link that
changes the input format hint to 10-char code and sets `method: "backup_code"`
on the POST body. The code is compared (bcrypt) against every unconsumed
backup code row for the user; on match the row is marked `consumed_at =
now()`. If this is the last unconsumed code, a warning banner is shown and
the `mfa.backup_codes.exhausted` event fires.

### 2.6 Admin reset

Admin with `admin.users.reset_2fa` on an AdminUsersPage row clicks "Reset
2FA" in the row menu (next to "Reset password", "Unlock"). Confirmation
modal warns the target will be forced to re-enroll on next login. On
confirm, backend deletes `user_totp` + `user_backup_code` rows for that
user, bumps `password_changed_at` (invalidates existing JWTs), and emits
`mfa.admin_reset`.

### 2.7 Tenant toggle (runtime)

Admin with `system.settings` on Settings → Security toggles "Require 2FA
for all users". Stored in `app_settings` under key `require_2fa`. Turning
it on does not invalidate current sessions, but the next login for any
user without a confirmed factor enters forced-enrollment (§2.3).

### 2.8 Disable / regenerate (self-service)

If tenant `require_2fa` is OFF, a user may disable their own 2FA from
Profile → Security (requires re-entering password + current TOTP).
Regeneration of backup codes is always allowed when 2FA is confirmed; it
replaces the entire set and invalidates all prior codes.

---

## 3. Data model

### 3.1 New table: `user_totp`

One row per user (FK `user_id` unique). Rows are only inserted after a
successful confirmation (per D11, pre-confirmation state is held
client-side for the duration of the wizard, never in the DB).

| column              | type           | notes                                              |
|---------------------|----------------|----------------------------------------------------|
| id                  | String(36) PK  | uuid4                                              |
| user_id             | String(36) FK  | `user.id` ON DELETE CASCADE, UNIQUE                |
| secret_encrypted    | Text NOT NULL  | Fernet-encrypted base32 secret (existing `ENCRYPTION_KEY`) |
| algorithm           | String(16)     | default `'SHA1'` (RFC 6238 / D10)                  |
| digits              | Integer        | default 6 (D10)                                    |
| period_seconds      | Integer        | default 30 (D10)                                   |
| enrolled_at         | DateTime tz    | NOT NULL — row exists ⇒ user is enrolled           |
| last_used_at        | DateTime tz    | anti-replay — see §10                              |
| last_used_counter   | BigInteger     | the TOTP counter (= unix/period) of the last successful code; NULL until first use |
| created_at          | DateTime tz    | default now()                                      |
| updated_at          | DateTime tz    | onupdate now()                                     |

Index: unique on `user_id` (enforces one row per user).

### 3.2 New table: `user_backup_code`

One row per backup code. Generated as a batch of N (default 10) at
enrollment confirmation, rotated atomically on regenerate.

| column        | type          | notes                                          |
|---------------|---------------|------------------------------------------------|
| id            | String(36) PK | uuid4                                          |
| user_id       | String(36) FK | `user.id` ON DELETE CASCADE, indexed           |
| code_hash     | String(60)    | bcrypt hash of the plaintext code (cost 12)    |
| created_at    | DateTime tz   | default now()                                  |
| consumed_at   | DateTime tz   | NULL until redeemed                            |
| consumed_ip   | String(45)    | captured for audit on consume                  |

Index: `(user_id, consumed_at)` for "count unconsumed".

Plaintext format: `secrets.token_urlsafe(7)` → ~10 chars, URL-safe alphabet,
shown grouped as `xxxxx-xxxxx` in the UI.

### 3.3 Tenant settings — reuse `app_settings` (migration 0018)

The DB-backed settings service (`backend/app/services/settings/registry.py`,
`backend/app/services/settings/service.py`) already exists. Add one new
registered key:

| key           | category | type | default | env_var       | restart_required |
|---------------|----------|------|---------|---------------|------------------|
| `require_2fa` | security | bool | `false` | `REQUIRE_2FA` | false            |

Resolution order already implemented: DB row wins, falls back to env-var
seeded default at first boot via `SettingsService.seed_from_env()`. No new
settings-store table needed — this matches the locked decision D1.

### 3.4 Migration (proposed `0025_add_user_totp_and_backup_codes.py`)

Follows the style of `0024_create_invitation_tokens_and_user_lifecycle.py`
(`backend/alembic/versions/0024_create_invitation_tokens_and_user_lifecycle.py:1-60`):

- `revision = "0025"`, `down_revision = "0024"`.
- `op.create_table("user_totp", ...)` with columns above.
- `op.create_table("user_backup_code", ...)` with index
  `ix_user_backup_code_user_consumed` on `(user_id, consumed_at)`.
- No change to `user` table (FKs only). Columns on related rows are not
  added; the "must enrol on next login" behaviour is derived from
  `user_totp IS NULL AND tenant require_2fa`.
- Down-migration drops both tables.

Safety: both tables are **additive and nullable** against existing user
rows. No existing row becomes invalid. Turning `require_2fa` on later
produces forced-enrollment at next login (§2.3) but does NOT invalidate
current sessions (see §12).

---

## 4. Backend API surface

All routes live under `backend/app/api/auth_2fa.py` (new module) except the
admin reset, which sits in `backend/app/api/admin_users.py` alongside the
existing `unlock` / `reset-password` handlers.

### 4.1 Enrollment

**`POST /api/auth/2fa/enroll/start`** — auth required (`get_current_user`).

- Request: `{}`
- Response 200:
  ```json
  {
    "secret_base32": "JBSWY3DPEHPK3PXP",
    "otpauth_uri": "otpauth://totp/SFBL%20(loader.example.com):alice@x.com?secret=...&issuer=SFBL&algorithm=SHA1&digits=6&period=30",
    "qr_svg": "<svg ...>"
  }
  ```
- Errors: 409 `already_enrolled` if the caller already has a `user_totp`
  row (use `/disable` first, allowed only when tenant toggle is off, or go
  via admin reset).

**`POST /api/auth/2fa/enroll/confirm`** — auth required.

- Request: `{ "secret_base32": "JBSWY3DPEHPK3PXP", "code": "123456" }` —
  the client echoes back the secret returned by `/enroll/start` (per D11
  no server-side pending row exists).
- Response 200:
  ```json
  {
    "access_token": "<new full-access JWT>",
    "expires_in": 3600,
    "backup_codes": ["xxxxx-xxxxx", ...]
  }
  ```
  (10 backup codes per D9.) The frontend **must** replace its stored
  token with the returned `access_token`; the bumped
  `password_changed_at` invalidates the caller's prior token per
  `backend/app/services/auth.py:154`.
- Errors: 400 `invalid_secret` (malformed base32), 400 `invalid_code`,
  409 `already_enrolled`.
- Side effects: inserts `user_totp` and 10 `user_backup_code` rows
  atomically; bumps `user.password_changed_at`; issues a fresh access
  token with `iat > password_changed_at`; emits `mfa.enroll.success`.

**`POST /api/auth/2fa/backup-codes/regenerate`** — auth required, requires
confirmed TOTP. Re-verifies current TOTP code in the request body.

- Request: `{ "code": "123456" }`
- Response 200: `{ "backup_codes": [...] }`
- Deletes all prior `user_backup_code` rows for the user (regardless of
  consumed_at) and writes the new set atomically in one transaction.

**`POST /api/auth/2fa/disable`** — auth required, allowed **only** if
tenant `require_2fa` is OFF.

- Request: `{ "password": "...", "code": "123456" }`
- Response 204 on success; 403 `tenant_enforced` if toggle is on.
- Deletes `user_totp` and all `user_backup_code` rows.

### 4.2 Login challenge (two-phase)

**`POST /api/auth/login`** — **modified** (see
`backend/app/api/auth.py:74-342`).

Branch precedence (same as §2.2):

1. **Enrolled branch** — user has a `user_totp` row. Respond with
   `mfa_methods: ["totp", "backup_code"]`, `must_enroll: false`.
2. **Forced-enroll branch** — no `user_totp` row AND tenant
   `require_2fa` is on. Respond with `mfa_methods: ["enroll"]`,
   `must_enroll: true`.
3. **No-MFA branch** — return the full `TokenResponse` as today.

MFA response shape:

```json
{
  "mfa_required": true,
  "mfa_token": "<short-TTL JWT>",
  "mfa_methods": ["totp", "backup_code"],
  "must_enroll": false
}
```

- `mfa_token` is a JWT with claims `{ sub: user_id, mfa_pending: true,
  must_enroll: <bool>, iat, exp }`, TTL 5 min, signed with the same
  secret as access tokens.
- `get_current_user` must reject tokens with `mfa_pending=true` so this
  token cannot access any other endpoint. A new
  `get_mfa_pending_user` dependency is used only by the three
  login/2fa routes.

Side-effect rules (see §2.4): on the MFA-required branches, the endpoint
issues the `mfa_token` and writes **one** `login_attempt` row with
`outcome="mfa_challenge_issued"` plus an `auth.login.mfa_challenge_issued`
event. It does NOT call `handle_successful_login()`, does NOT reset
lockout counters, does NOT write a `succeeded` audit row, does NOT
update `last_login_at`, and does NOT handle `must_reset_password` — all
of those move to phase 2. Failed-password and rate-limiting behaviour
is unchanged (they happen before the MFA branch is selected).

**`POST /api/auth/login/2fa`** — new. Gated by `get_mfa_pending_user`
(claim `mfa_pending=true`, `must_enroll=false`).

- Request:
  ```json
  { "mfa_token": "...", "code": "123456", "method": "totp" }
  ```
  `method` is `"totp"` or `"backup_code"`.
- Response 200: standard `TokenResponse` (as today). Phase-2 success
  side effects fire here per §2.4 — `handle_successful_login()`,
  `login_attempt` success row, `auth.login.succeeded` event,
  `last_login_at` update, `must_reset_password` honoured.
- Errors:
  - 401 `mfa_token_invalid` — signature, expired, or not `mfa_pending`.
  - 401 `mfa_code_invalid` — TOTP mismatch or all backup codes exhausted.
  - 429 — subject to a dedicated per-user 2FA verify rate limit (§10).

Per-user failure accounting: failed 2FA verifies call
`auth_lockout.handle_failed_attempt()` so they **count toward the same
progressive-lockout counters** used by failed password attempts
(`services/auth_lockout.py`). Rationale: prevents a bypass where an
attacker who has the password can grind TOTP codes indefinitely.

**`POST /api/auth/login/2fa/enroll/start`** — new, used only in forced
enrollment (§2.3). Gated by `get_mfa_pending_user` (claim
`mfa_pending=true`, `must_enroll=true`). Separate from the self-service
`/api/auth/2fa/enroll/start` because the caller does not yet hold a
full-access JWT.

- Request: `{ "mfa_token": "..." }`
- Response 200: `{ secret_base32, otpauth_uri, qr_svg }` (in-memory,
  no DB row per D11).
- Errors: 401 `mfa_token_invalid`, 403 `not_forced_enroll` if the
  token does not carry `must_enroll=true`.
- Side effects: none beyond an `auth.login.mfa_enroll_started` event.

**`POST /api/auth/login/2fa/enroll-and-verify`** — new. Same auth
gate as `/login/2fa/enroll/start`.

- Request: `{ "mfa_token": "...", "secret_base32": "...", "code": "123456" }`
- Validates `mfa_token` (must carry `mfa_pending=true` and
  `must_enroll=true`), verifies the 6-digit code against the supplied
  `secret_base32` (±1 step window), then atomically inserts the
  `user_totp` row and 10 `user_backup_code` rows, applies all phase-2
  success side effects (§2.4), and returns the full-access
  `TokenResponse` plus `{ backup_codes: [...] }` shown once.
- Errors: 401 `mfa_token_invalid`, 400 `invalid_secret`, 400
  `invalid_code`, 409 `already_enrolled` (race — another session enrolled
  first).

### 4.3 Admin

**`POST /api/admin/users/{user_id}/reset-2fa`** — permission
`admin.users.reset_2fa` (new key — see §6).

- Request: `{}`
- Response 204.
- Effects: deletes `user_totp` and all `user_backup_code` rows for target;
  bumps target's `password_changed_at`; writes a login_attempt-style audit
  row (reuse `login_attempt` with `outcome="admin_reset_2fa"`); emits
  `mfa.admin_reset`.

### 4.4 Tenant setting

Reuses the generic settings API:

- **`GET /api/settings/security`** — already returns the security category;
  add `require_2fa` to the response DTO.
- **`PUT /api/settings/security`** — already supports partial updates; the
  handler validates via the registry.

A change to `require_2fa` emits `mfa.tenant_toggle.changed` with
`{ old, new, actor_user_id }`.

### 4.5 Profile surface

`GET /api/auth/me` (`backend/app/api/auth.py:345-387`) gains one field:

```jsonc
{
  // ...existing...
  "mfa": {
    "enrolled": true,
    "enrolled_at": "2026-04-24T...",
    "backup_codes_remaining": 7
  }
}
```

---

## 5. Login flow — sequence (ASCII)

### 5.1 TOTP happy path

```
Client                          API                         DB
  │                              │                           │
  │── POST /login (email,pw) ───▶│                           │
  │                              │── select user by email ──▶│
  │                              │◀────── user row ──────────│
  │                              │ verify_password() OK      │
  │                              │── select user_totp ──────▶│
  │                              │◀─ user_totp row exists ───│
  │◀── 200 {mfa_required, mfa_token} ─│                      │
  │                              │                           │
  │── POST /login/2fa ──────────▶│                           │
  │   (mfa_token, code)          │ decode mfa_token          │
  │                              │ verify TOTP (±1 step)     │
  │                              │── update last_used_*  ───▶│
  │                              │── reset lockout counters ▶│
  │◀── 200 {access_token, ...} ──│                           │
```

### 5.2 Backup-code path

```
Client                          API                         DB
  │── POST /login/2fa ──────────▶│                           │
  │   (mfa_token, code,          │                           │
  │    method=backup_code)       │ select unconsumed codes ▶│
  │                              │ bcrypt.checkpw(code, h)   │
  │                              │── mark row consumed ─────▶│
  │                              │ if last one ─▶ emit       │
  │                              │  mfa.backup_codes.exhausted│
  │◀── 200 {access_token, ...} ──│                           │
```

### 5.3 Failure branches

- **Wrong password** — identical to today: 401 at `/login`, 2FA challenge is
  never issued, lockout counters increment.
- **Wrong TOTP** — 401 `mfa_code_invalid` at `/login/2fa`, lockout counters
  increment via `handle_failed_attempt`, `login_attempt` row written with
  `outcome="wrong_mfa"`.
- **Expired mfa_token** — 401 `mfa_token_invalid`; user must re-enter
  password.
- **Backup code exhausted** — 401 `mfa_code_invalid` + banner on the login
  page pointing to "contact your admin to reset 2FA". Event
  `mfa.backup_codes.exhausted` is emitted on the transition from 1→0, not
  on every failed lookup after 0.
- **Forced-enrollment** — `must_enroll: true` in the response; client routes
  to `/login/mfa-enroll`.

---

## 6. Frontend surface

### 6.1 New / modified pages

| Page / component                                    | Status   | Notes                                                        |
|-----------------------------------------------------|----------|--------------------------------------------------------------|
| `frontend/src/pages/Login.tsx`                      | modified | Handle `mfa_required` branch; route to MFA challenge screen. |
| `frontend/src/pages/LoginMfaChallenge.tsx`          | new      | TOTP / backup code input; uses `mfa_token` from state.       |
| `frontend/src/pages/LoginMfaEnroll.tsx`             | new      | Forced-enrollment at login (§2.3).                           |
| `frontend/src/pages/Profile.tsx`                    | modified | Add Security section with enroll / disable / regenerate.     |
| `frontend/src/pages/MfaEnrollWizard.tsx`            | new      | 3-step wizard: QR → confirm → backup codes.                  |
| `frontend/src/pages/MfaBackupCodesModal.tsx`        | new      | Shown after enrollment and regenerate.                       |
| `frontend/src/pages/AdminUsersPage.tsx`             | modified | Add "Reset 2FA" row action alongside existing unlock/reset-password (`frontend/src/pages/AdminUsersPage.tsx:483,622`). |
| `frontend/src/pages/SettingsSecurityPage.tsx`       | modified | Add `require_2fa` toggle control.                            |

### 6.2 API layer

- `frontend/src/api/endpoints.ts` — add `authApi.login2fa`,
  `authApi.mfaEnrollStart`, `authApi.mfaEnrollConfirm`,
  `authApi.mfaDisable`, `authApi.mfaRegenBackup`, `adminUsersApi.reset2fa`.
- `frontend/src/api/types.ts` — new types `MfaChallengeResponse`,
  `MfaEnrollStartResponse`, `MfaEnrollConfirmResponse`,
  `MfaRequiredLoginResponse`; modify `TokenResponse` to be a union of
  full-auth and mfa-required variants (or wrap both in a discriminated
  union on `mfa_required`).

### 6.3 Navigation / nav menu

No new primary route. `/profile` gains a Security section; Settings →
Security gains the tenant toggle; admin users row gains the row action.

### 6.4 Help content

New usage topic `docs/usage/two-factor-auth.md` with frontmatter fields per
the usage authoring contract (`CLAUDE.md` §Documentation Policy):

- `slug: two-factor-auth`, `nav_order: 35` (between account-recovery and
  user-management), `required_permission: null` (visible to all
  authenticated users — content is generic).
- Covers: enrolling, using TOTP at login, using a backup code, regenerating
  backup codes, what to do if locked out.
- Must pass `node frontend/scripts/check-help-links.mjs`.

An admin subtopic goes into `docs/usage/user-management.md` (existing) —
add a "Resetting a user's 2FA" section cross-linked from the main help.

---

## 7. Observability plan

### 7.1 Canonical events (add to `backend/app/observability/events.py`)

New class `MfaEvent` alongside the existing `AuthEvent`. Additionally,
one event extends the existing `AuthEvent` class so the login pipeline
has a clean phase-1 audit marker (§2.4):

```
auth.login.mfa_challenge_issued — password ok, mfa_token issued (phase 1)
auth.login.mfa_enroll_started   — forced-enrol /login/2fa/enroll/start called
```

`MfaEvent` — all other 2FA-specific events:

```
mfa.enroll.started          — /enroll/start called
mfa.enroll.success          — /enroll/confirm succeeded
mfa.enroll.failed           — /enroll/confirm bad code
mfa.disabled                — user self-service disable
mfa.login.totp.success      — successful TOTP at /login/2fa
mfa.login.totp.failure      — wrong TOTP at /login/2fa
mfa.login.backup_code.used  — backup-code login succeeded
mfa.login.token_invalid     — mfa_token rejected
mfa.backup_codes.regenerated
mfa.backup_codes.exhausted  — transitioned unconsumed → 0
mfa.admin_reset             — admin cleared a user's 2FA
mfa.tenant_toggle.changed   — require_2fa flipped
```

### 7.2 Outcome codes (add to `OutcomeCode`)

```
mfa_code_invalid
mfa_token_invalid
mfa_token_expired
mfa_already_enrolled
mfa_invalid_secret
mfa_tenant_enforced
mfa_backup_codes_exhausted
mfa_replay_rejected
```

Existing `WRONG_PASSWORD`, `USER_LOCKED`, etc. from
`backend/app/observability/events.py:287-383` continue to cover the
password and lockout sides of the flow.

### 7.3 Metrics (add to `backend/app/observability/metrics.py`)

- `auth_mfa_verify_total{outcome, method}` — counter (method = `totp` /
  `backup_code`).
- `auth_mfa_enroll_total{outcome}` — counter.
- `auth_mfa_backup_codes_remaining_on_consume` — **histogram** emitted
  on every backup-code consume (value = remaining unconsumed codes
  **after** consumption). Chosen over a per-user gauge to avoid a
  cardinality blow-up on `user_id` labels. Per-user state is still
  visible to operators via the `/api/auth/me` response
  (`mfa.backup_codes_remaining`) and — more importantly — via the
  `mfa.backup_codes.exhausted` event fired on the 1→0 transition.
- `auth_mfa_tenant_required` — gauge (0/1), updated on toggle change.

### 7.4 Audit trail

Every 2FA attempt (success or failure) writes a row to `login_attempt` with
a new `outcome` value: `mfa_ok`, `wrong_mfa`, `mfa_token_expired`,
`backup_code_used`, `backup_code_exhausted`. This reuses the existing
infrastructure from `backend/app/api/auth.py:36-72`.

---

## 8. Config + env

Add one environment variable to `.env.example`:

```
# Two-factor authentication (TOTP) — tenant-wide enforcement.
# DB override (Settings → Security) takes precedence once set. Value here is
# used only until the first boot seeds it into app_settings.
REQUIRE_2FA=false
```

Registry entry (see §3.3). No changes to `Settings` in `config.py` beyond
adding a `require_2fa: bool = False` field so the env var can be seeded.

### 8.1 Interaction with distribution profiles

- `desktop` profile (`auth_mode == 'none'`): 2FA is unreachable —
  enrollment, login challenge, and admin reset endpoints all short-circuit
  to 404/204 with an explanatory event. No DB schema change skipped;
  migrations run as normal on SQLite.
- `self_hosted` and `aws_hosted`: 2FA active.

---

## 9. Libraries

### 9.1 Backend

- **`pyotp`** (Apache 2.0) — TOTP generation/verification. Small (~200
  LOC), well-maintained, no native deps.
- **`segno`** (BSD) — SVG QR code generation. Pure Python, no Pillow
  dependency. Chosen over `qrcode[pil]` because Pillow is a heavy
  binary dependency we don't currently pull in.
- **`bcrypt`** — **already in the dependency tree** (used for password
  hashing). Reused for backup-code hashes. No new lib.
- **Fernet** via `cryptography` — already used (`backend/app/utils/encryption.py`).
  Reused for the TOTP secret at rest via the existing `ENCRYPTION_KEY`.

### 9.2 Frontend

No new libraries. QR is rendered backend-side as inline SVG — the frontend
just drops it into the DOM via `dangerouslySetInnerHTML` after sanitising
with the existing helper (or renders as a `<img src="data:image/svg+xml;base64,..."/>`
if safer). Rationale for backend-rendered QR: we already own the secret on
the server at `/enroll/start` time; avoiding a client-side QR lib keeps the
frontend bundle unchanged and guarantees the QR and otpauth URI are
derived from a single source of truth.

---

## 10. Security considerations

### 10.1 Anti-replay

Store `last_used_counter` (unix_time // period) on `user_totp`. Reject any
code whose counter is `<= last_used_counter`. Combined with the standard
±1 step window in `pyotp.TOTP.verify(valid_window=1)`, this prevents a
captured code from being used twice within its 30-second window. Outcome
`mfa_replay_rejected`.

### 10.2 Rate limiting

- Per-user 2FA verify: 10 attempts per 5 minutes, reuse
  `services/rate_limit.py` keyed by `2fa:user:{user_id}`.
- Per-IP: already covered by the existing login rate limit (20/5 min)
  because `/login/2fa` is reached only after `/login` passed the same
  limiter.
- On rate limit breach: 429, emit `auth.login.rate_limited` with a new
  outcome `mfa_user_limit`.

### 10.3 Timing-safe comparison

Use `hmac.compare_digest` or `pyotp`'s built-in (which does this) for TOTP.
For backup codes, `bcrypt.checkpw` is constant-time over the bcrypt output
space; iterate over all unconsumed codes even after a match to avoid
short-circuit timing leaks — or, equivalently, always iterate the full set
and record the first match.

### 10.4 Backup-code entropy

`secrets.token_urlsafe(7)` yields ~56 bits of entropy per code, ~560 bits
across 10 codes. The grouping `xxxxx-xxxxx` is display-only; the DB stores
bcrypt of the raw token (without the hyphen).

### 10.5 Lockout interaction

Failed TOTP and failed backup code both call
`services/auth_lockout.handle_failed_attempt()` so they count toward the
same thresholds as failed passwords (`backend/app/api/auth.py:283`).
Successful 2FA calls `handle_successful_login()` — same as today.

### 10.6 mfa_token design

- JWT signed with the existing `jwt_secret_key`, claims
  `{ sub, iat, exp, mfa_pending: true, purpose: "mfa_challenge" }`.
- `get_current_user` rejects any token with `mfa_pending=true` — only
  `/login/2fa` and `/login/2fa/enroll-and-verify` accept it, via a
  dedicated `get_mfa_pending_user` dependency that explicitly looks for
  the claim.
- TTL 5 minutes. If the user needs longer, they re-enter their password.

### 10.7 Secret at rest

Fernet-encrypted base32 string via the existing `ENCRYPTION_KEY`
(`backend/app/utils/encryption.py`). Never logged, never returned on any
endpoint after `/enroll/start` (the only path that has plaintext access).

### 10.8 Recovery & break-glass

The break-glass CLI recovery (SFBL-193, `auth.admin.recovered`) must also
clear the recovered admin's 2FA — otherwise a lost password + lost
authenticator leaves the tenant unrecoverable. Add a
`--reset-2fa` flag (default true on the admin-recovery command).

---

## 11. Docs DoD (epic close-out)

- **Create** `docs/usage/two-factor-auth.md` with the frontmatter contract.
- **Update** `docs/usage/user-management.md` — add "Resetting a user's 2FA".
- **Update** `docs/usage/account-recovery.md` — describe backup-code and
  admin-reset paths.
- **Update** `docs/usage/settings.md` — document the `require_2fa` toggle.
- **Update** `docs/deployment/*.md` — note the new `REQUIRE_2FA` env var
  and recommend turning it on for hosted deployments.
- **Update** `docs/admin-recovery.md` — clarify that break-glass recovery
  also clears 2FA by default.
- **Update** `.env.example` with `REQUIRE_2FA=false`.
- **Update** `docs/ui-conventions.md` — document the backup-codes modal
  pattern and the MFA challenge layout if either introduces a new shared
  component.
- **Update** `docs/architecture.md` — one paragraph on the two-phase login
  with a reference to this spec.
- **Update** `docs/specs/rbac-permission-matrix.md` and its `.yml` — add
  `admin.users.reset_2fa`.
- **Run** `node frontend/scripts/check-help-links.mjs` before pushing.

---

## 12. Rollout

### 12.1 Migration safety

The migration is additive: two new tables, no alterations to `user` or
`profile`. Existing sessions and existing users are unaffected by running
the migration with `require_2fa=false`.

### 12.2 Enabling tenant enforcement

Flipping `require_2fa` on:

- Existing JWTs remain valid — we do NOT mass-invalidate. Rationale:
  that would log everyone out at the moment the admin flipped a switch,
  which is disruptive and not a security gain (current sessions were
  already holding a valid auth context before 2FA existed).
- On next login, any user without a confirmed factor enters forced
  enrollment (§2.3). Users with a confirmed factor see no change.
- Admins should announce the change via email / in-app banner. An
  optional "send enrollment reminder" broadcast is out of scope for this
  epic (follow-up).

### 12.3 Disabling tenant enforcement

Flipping off doesn't remove any user's existing 2FA; it just allows users
to self-disable and stops forcing enrollment at login.

### 12.4 Backwards compatibility

Clients on old frontend builds that don't understand `mfa_required: true`
will receive the 200 response and fail to parse `access_token`. Acceptable
because:

- The epic ships frontend + backend together (one PR).
- External API consumers don't exist for this app.

---

## 13. Out of scope / future

- **WebAuthn / passkeys** — the strongest factor, phishing-resistant. A
  future epic should add passkey support as an alternative to TOTP with
  the same two-phase login framing. The `mfa_methods` array in the
  challenge response is deliberately a list to make that addition
  non-breaking.
- **Per-role enforcement** — e.g. require 2FA only for admin profile.
- **Push-notification auth** (Duo-style) — requires a trusted mobile
  channel we don't have.
- **"Remember this device"** — intentionally excluded per D4.
- **Session kill on toggle-on** — see §12.2.

---

## 14. Proposed child tickets

Epic title: **SFBL-2FA: TOTP two-factor authentication**

Wave 1 — foundation:

**2FA-1: data model, migration, and tenant setting (S)**
Introduce `user_totp` and `user_backup_code` tables; add `require_2fa`
registry entry; no behaviour changes yet. Files: `backend/alembic/versions/0025_*.py`
(new), `backend/app/models/user_totp.py` (new), `backend/app/models/user_backup_code.py`
(new), `backend/app/services/settings/registry.py`, `backend/.env.example`.
No sibling dependencies.

**2FA-2: `/api/auth/me` shape extension (S)**
Add the `mfa: { enrolled, enrolled_at, backup_codes_remaining }` sub-object
to the `/api/auth/me` response and its Pydantic schema. Ships early so
frontend and other consumers of `/me` absorb the additive shape change
independently of the enrollment API landing. Files: `backend/app/api/auth.py`,
`backend/app/schemas/auth.py`, `frontend/src/api/types.ts` (add field as
optional). Depends on 2FA-1.

**2FA-3: TOTP service + enrollment API (M)**
Add `services/totp.py` (secret gen, verify, anti-replay), QR SVG renderer
using `segno`, and the `/2fa/enroll/start`, `/2fa/enroll/confirm`,
`/2fa/disable`, `/2fa/backup-codes/regenerate` endpoints. Stateless
enrollment per D11 (no DB row until confirm). Reuse Fernet for secret at
rest. Files: `backend/app/services/totp.py` (new),
`backend/app/api/auth_2fa.py` (new), `backend/app/schemas/auth_2fa.py`
(new), new pyotp + segno deps in `backend/pyproject.toml`. Depends on 2FA-1.

Wave 2 — login integration:

**2FA-4: two-phase login flow + lockout integration (M)**
Modify `/api/auth/login` to emit the `mfa_required` branch; add
`/api/auth/login/2fa` and `/api/auth/login/2fa/enroll-and-verify`;
implement `mfa_pending` JWT claim; wire failed 2FA into the
progressive-lockout service. Files: `backend/app/api/auth.py`,
`backend/app/api/auth_2fa.py`, `backend/app/services/auth.py`,
`backend/app/services/auth_lockout.py`, `backend/app/schemas/auth.py`.
Depends on 2FA-3.

**2FA-5: admin reset + new permission key (S)**
Add `admin.users.reset_2fa` to `permissions.py`, seed the admin profile
with it, add `POST /api/admin/users/{id}/reset-2fa`, update
`docs/specs/rbac-permission-matrix.md` / `.yml`, and wire the break-glass
CLI to clear 2FA by default (per D8, this is the documented recovery
path when `require_2fa` is on and the user has lost their
authenticator). Files: `backend/app/auth/permissions.py`,
`backend/app/api/admin_users.py`, `backend/app/cli/admin_recovery.py` (or
equivalent), `docs/specs/rbac-permission-matrix.*`. Depends on 2FA-1.

Wave 3 — frontend and rollout:

**2FA-6: frontend enrollment + profile security UI (M)**
`MfaEnrollWizard` (stateless — client retains `secret_base32` for the
duration of the wizard per D11), `MfaBackupCodesModal`, Profile Security
section, API client additions. Files: `frontend/src/pages/Profile.tsx`,
`frontend/src/pages/MfaEnrollWizard.tsx` (new),
`frontend/src/pages/MfaBackupCodesModal.tsx` (new),
`frontend/src/api/endpoints.ts`, `frontend/src/api/types.ts`. Depends on
2FA-3.

**2FA-7: frontend login challenge + forced enrollment + tenant toggle + admin row action (M)**
Modify `Login.tsx`, add `LoginMfaChallenge.tsx`, add `LoginMfaEnroll.tsx`,
add `require_2fa` control to `SettingsSecurityPage.tsx`, add "Reset 2FA"
row action to `AdminUsersPage.tsx`. Files: the five listed plus
`frontend/src/api/endpoints.ts`. Depends on 2FA-4, 2FA-5, 2FA-6.

**2FA-8: observability, docs, help content, rollout polish (S)**
Add `MfaEvent` class and new outcome codes; add metrics; new
`docs/usage/two-factor-auth.md`; update `user-management.md`,
`account-recovery.md`, `settings.md`, `.env.example`, deployment guides,
`docs/architecture.md`, `docs/ui-conventions.md`; run
`check-help-links.mjs`. Files: `backend/app/observability/events.py`,
`backend/app/observability/metrics.py`, `docs/usage/*.md`,
`docs/deployment/*.md`, `docs/architecture.md`, `docs/ui-conventions.md`,
`.env.example`. Depends on all prior.

Total: 8 child stories. Rough complexity: 4×S + 4×M, no L.
