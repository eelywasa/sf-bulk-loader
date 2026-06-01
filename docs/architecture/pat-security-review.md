# PAT security review — SFBL-357 / SFBL-370

## Review summary

Two independent security review passes were performed on the PAT authentication
implementation (SFBL-357 epic: tickets SFBL-366 through SFBL-370) as part of
the epic's definition of done.

- **Pass 1** — automated: Codex static analysis against the PR diff.
- **Pass 2** — manual: deep reading of `backend/app/services/pat.py`,
  `backend/app/services/auth.py` (`_authenticate_pat`), and
  `backend/app/api/me_tokens.py`, cross-referenced against OWASP API
  Security Top 10 items relevant to token issuance, storage, and auth.

**Result: no escalation.** The implementation is clean. One Low finding and
one hardening item identified in Pass 2 were remediated in commit `ed950aa`
before this sign-off was written. No Medium or High findings were raised in
either pass.

---

## Checklist

### Token generation

- [x] **256-bit entropy** — tokens use `secrets.token_urlsafe(32)` (32 bytes =
  256 bits), which is well beyond the minimum 128-bit NIST SP 800-132 guidance
  for high-entropy tokens. Brute-force is computationally infeasible.
- [x] **CSPRNG** — `secrets.token_urlsafe` uses the OS CSPRNG (`os.urandom`),
  not `random`.
- [x] **Recognisable prefix** — `sfbl_pat_` prefix enables secret-scanning
  tools (GitHub, truffleHog, etc.) to alert on accidental commits or leaks.

### Token storage

- [x] **Plaintext not stored** — only `HMAC-SHA256(HKDF(ENCRYPTION_KEY), plaintext)`
  is persisted. The plaintext cannot be recovered from the database.
- [x] **Key isolation** — the HMAC signing key is derived from `ENCRYPTION_KEY`
  via HKDF-SHA256 with info label `b"sfbl-pat-hmac-v1"`, cryptographically
  separating it from the Fernet key used to encrypt Salesforce private keys.
- [x] **Fast O(1) lookup without per-row salting** — the keyed HMAC is
  deterministic (the server key acts as the salt for the whole population),
  enabling a direct unique-index lookup. This is correct because the
  adversary's goal is to obtain the server key, not to crack individual tokens
  from a stolen hash dump; with a 256-bit token there is no practical hash
  attack even without per-token salting.
- [x] **Constant-time comparison** — `hmac.compare_digest(stored, computed)` is
  used after the index lookup to prevent timing side-channels on partial
  string equality.

### Authentication flow

- [x] **No JWT confusion** — the PAT branch is entered before JWT decode is
  attempted. A PAT bearer value (not a valid JWT) does not cause a JWTError
  that could be misread as a different failure.
- [x] **Revocation gate** — `revoked_at IS NOT NULL` immediately rejects the
  token.
- [x] **Expiry gate** — `expires_at IS NOT NULL AND expires_at <= now` rejects
  the token; timezone-aware comparison.
- [x] **Status + lockout gates** — re-applies the same `status='active'` and
  `locked_until` checks as the JWT path. Account deactivation or tier-1
  auto-lock immediately blocks all PAT-authenticated requests.
- [x] **Profile eager-loaded** — `selectinload(User.profile)` guarantees the
  owner's permission set is available for `require_permission()`. Without this,
  `profile=None` causes a 403 for all permission checks.
- [x] **Token material never logged** — observability records carry only
  `pat_id` and `pat_last4` (last 4 chars of the full token string). The
  plaintext is not in any `_log.*` call, which is enforced by the
  `sanitization.py` rules and verified by `test_no_token_material_in_logs`.

### Session-only gating (creation / revocation)

- [x] **Session required to mint** — `POST /api/me/tokens` is gated by
  `require_session_auth`, which reads `request.state.auth_method` and accepts
  only `"session"`. A PAT-authenticated request receives `403 session_required`.
- [x] **Session required to revoke** — `DELETE /api/me/tokens/{id}` carries
  the same `require_session_auth` dependency.
- [x] **Fail-closed** — `require_session_auth` rejects any value other than
  `"session"` (including `None` and any future unrecognised method). See
  `test_require_session_auth_fails_closed`.
- [x] **Ownership check** — `DELETE` verifies `token.user_id == current_user.id`
  before revoking; cross-user revocation returns 403.

### Transport policy

- [x] **Header-only transport** — `get_current_user` uses `HTTPBearer(auto_error=False)`,
  which reads exclusively from the `Authorization: Bearer` header. There is no
  code path that reads a PAT from a query parameter.
- [x] **Query-parameter transport rejected** — confirmed by
  `test_pat_query_param_not_accepted` in `backend/tests/test_pat_auth.py`.
  Sending a valid PAT as `?token=…` or `?access_token=…` returns 401; only the
  header path authenticates.
- [x] **Policy documented** — the query-parameter prohibition is documented in
  both `docs/usage/personal-access-tokens.md` and
  `docs/architecture/auth-and-rbac.md`.

### Rate limiting

**Decision: no per-PAT-request throttle in v1.**

Reasoning:
- Tokens are 256-bit (`secrets.token_urlsafe(32)`). Brute-forcing the HMAC
  index requires finding a 256-bit string whose HMAC-SHA256 matches a stored
  hash. With a secret server key, this is equivalent to breaking HMAC-SHA256
  with a 256-bit key — computationally infeasible regardless of request rate.
- The `locked_until` progressive-lockout mechanism applies to **login
  failures** (password authentication), not PAT authentication. This is
  correct: PAT failures do not count towards the login-failure threshold
  because PAT authentication does not involve credentials that can be
  brute-forced via repeated API calls at realistic request rates.
- Adding per-IP throttling on PAT auth would add latency and complexity
  without improving the security posture, because the token space is too large
  to enumerate.

This decision should be revisited if PAT scopes are introduced (narrower
scope → lower blast radius → different threat model) or if per-org tenancy
requires rate fencing between customers.

### Observability

- [x] **PAT_ISSUED** (`auth.pat_issued`) — emitted in `pat_service.issue()` on
  successful creation.
- [x] **PAT_USED** (`auth.pat_used`) — emitted in `_authenticate_pat()` on
  every successful PAT-authenticated request (write-throttled at most once per
  5-minute window to limit DB writes on high-frequency callers).
- [x] **PAT_REVOKED** (`auth.pat_revoked`) — emitted in `pat_service.revoke()`
  on first revocation; silent on idempotent re-revocation (preserving original
  audit timestamp).
- [x] **TOKEN_REJECTED** (`auth.token_rejected`) — emitted with
  `rejection_reason` for unknown, revoked, expired, inactive-user, and
  locked-user PAT rejection paths.

### No-escalation verdict

Both review passes agree the PAT implementation meets the security baseline for
v1. The remediation in `ed950aa` addressed the two items from Pass 2 (a Low
finding and a hardening improvement). No residual findings.

---

## Remediation record (ed950aa)

The commit `ed950aa` applied two fixes identified in the Pass 2 manual review:

1. **Low finding (fixed)** — details recorded in the Jira ticket SFBL-370.
2. **Hardening (fixed)** — details recorded in the Jira ticket SFBL-370.

The commit is on the `feat/sfbl-357-pat` branch and will be included in the
epic PR.

---

## Related

- [`docs/architecture/auth-and-rbac.md`](auth-and-rbac.md) — PAT authentication
  section for the full technical description
- [`docs/usage/personal-access-tokens.md`](../usage/personal-access-tokens.md)
  — operator handbook
- [`backend/app/services/pat.py`](../../backend/app/services/pat.py) — token
  issuance and hashing service
- [`backend/app/services/auth.py`](../../backend/app/services/auth.py)
  `_authenticate_pat` — authentication middleware
- [`backend/app/api/me_tokens.py`](../../backend/app/api/me_tokens.py) — management API
- Test coverage: `backend/tests/services/test_pat.py`,
  `backend/tests/test_pat_auth.py`, `backend/tests/test_me_tokens.py`
