# Profile & Password Reset — Architecture Decisions (SFBL-119)

This document records key design decisions made across the SFBL-119 child tickets
(SFBL-145 through SFBL-151). It is a companion to `docs/specs/auth-spec.md`.

---

## Decision 001 — No dedicated `audit_log` table (SFBL-151)

**Decision:** Do not introduce a new `audit_log` database table for password-reset
and email-change operations in v1.

**Rationale:**

1. **Structured log events** — every state-changing operation emits a structured
   log record with `event_name` (from `AuthEvent`), `outcome_code` (from
   `OutcomeCode`), `user_id`, and `token_id`. In a production deployment these
   records are shipped to a log aggregator (Loki, CloudWatch Logs, etc.) and are
   queryable.

2. **Prometheus counters** — five new counters track volume and outcome distribution
   for every auth flow:
   - `sfbl_auth_password_reset_requests_total{outcome}`
   - `sfbl_auth_password_reset_confirms_total{outcome}`
   - `sfbl_auth_password_changes_total{outcome}`
   - `sfbl_auth_email_change_requests_total{outcome}`
   - `sfbl_auth_email_change_confirms_total{outcome}`

3. **`email_delivery` rows** — every email dispatched (reset link, email-change
   verify, notice) creates a row in `email_delivery` with an idempotency key
   that ties it back to the token. This provides a durable, queryable record of
   what was sent and when.

4. **Token tables** — `password_reset_token` and `email_change_token` already
   record `created_at`, `expires_at`, `used_at`, `request_ip`, and the SHA-256
   token hash. These rows satisfy basic audit requirements without a separate
   table.

**What this does NOT cover:**

- Long-term audit retention independent of the log pipeline.
- Who viewed the profile page (not required for v1).
- Admin-initiated actions on behalf of users.

**If requirements grow:** add an `audit_event` table in a future ticket. The
`AuthEvent` constants and `OutcomeCode` values defined in SFBL-151 are stable
identifiers that can be stored directly in that table without a migration of
log formats.

---

## Decision 002 — JWT watermark via `password_changed_at` (SFBL-145/146/147)

**Decision:** Use a `password_changed_at` column on `User` as a JWT invalidation
watermark rather than maintaining a token deny-list or incrementing a counter.

**Rationale:**

- Zero additional queries per request: the watermark is loaded as part of the
  existing `User` row fetch in `get_current_user`.
- No external state (Redis, extra table) required.
- The check is a simple integer comparison: `iat < password_changed_at.timestamp()`.
- Truncated to whole seconds so the freshly-issued token (same-second `iat`) is
  never rejected by a strict `<` comparison.

**Limitation:** all sessions are invalidated on any password change, not just
sessions on other devices. This is the correct security posture for a v1 admin
tool.

---

## Decision 003 — Non-enumeration on password-reset requests (SFBL-147)

**Decision:** `POST /api/auth/password-reset/request` always returns `202 Accepted`
regardless of whether the submitted email matches a user account.

**Rationale:** prevents an unauthenticated attacker from confirming which email
addresses are registered. The structured log records `outcome_code: unknown_email`
internally for monitoring, but the HTTP response is identical in both cases.

---

## Decision 004 — Single-use hashed tokens (SFBL-147/148)

**Decision:** Reset and email-change tokens are stored as SHA-256 digests, never
as raw values. Tokens are single-use: `used_at` is stamped atomically with the
state change they authorise.

**Rationale:**

- Hash storage means a database read does not expose the raw token.
- `token_hash` is safe in telemetry (cannot be reversed); `token` / `raw_token`
  are denied by the sanitisation layer.
- Single-use prevents replay if a confirmation link is accessed more than once.

---

## Decision 005 — Verify-before-commit email change with two-address notice (SFBL-148)

**Decision:** Email changes follow a verify-before-commit pattern:

1. A verification link is sent to the **new** address.
2. A change-notice email is sent to the **current** address (so the account holder
   is aware even if the request was made by an attacker with a live session).
3. The user's `email` column is updated only after the verify link is clicked.
4. `password_changed_at` is NOT bumped on email change — existing JWTs remain valid.

**Rationale:** the verify-before-commit pattern prevents an attacker from locking
a legitimate user out of their account by changing the email while holding a
stolen session. The two-address notice gives the legitimate user a recovery window.

---

## Decision 006 — In-memory sliding-window rate limiter (SFBL-147/148)

**Decision:** Rate limiting for password-reset and email-change requests uses a
simple in-process sliding-window counter (`app.services.rate_limit`) rather than
a Redis-backed solution.

**Rationale:**

- The application is designed for single-instance deployments (Docker Compose,
  Desktop). A Redis dependency would increase operational complexity.
- The limits are advisory safeguards against accidental spam, not security
  perimeter enforcement. Production deployments behind a load balancer should
  add network-level rate limiting at the edge.

**Limitation:** rate limits do not survive process restarts and are not shared
across multiple app replicas.
