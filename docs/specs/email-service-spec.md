# Spec: Outbound Email Service

**Jira Epic:** _to be created_ (SFBL — "Outbound Email Service")
**Status:** Draft — decisions locked, implementation pending
**Dependents:** SFBL-117 (Run-Complete Notifications), SFBL-119 (User Profile & Password Reset)

---

## Overview

The backend has two imminent needs for outbound email:

1. **SFBL-117 — Run-Complete Notifications** needs to deliver plain-text/HTML
   summaries of terminal run state to subscribed users via SMTP or AWS SES.
2. **SFBL-119 — User Profile & Password Reset** needs to deliver
   single-use, time-limited reset-token links to hosted-profile users.

These requirements collide on a shared primitive — "send an email from the
backend" — that currently does not exist. This spec lifts that primitive into
its own service so both epics (and future consumers) share one
configuration surface, one delivery log, one template engine, and one
observability story.

Without this split, SFBL-117 would own an SMTP/SES adapter that auth must
then reach across an epic boundary to use; any change to retry semantics or
credential handling would need to be coordinated across two unrelated
delivery surfaces. This spec makes email delivery its own concern.

---

## Decisions (locked)

The following are fixed by design review on 2026-04-17.

| Topic | Decision |
|---|---|
| Public API shape | Single `EmailService` entrypoint with pluggable `EmailBackend` Protocol |
| Backend choices | `smtp`, `ses`, `noop` (default on desktop + unconfigured hosts) |
| Backend selection | Deployment-wide via `EMAIL_BACKEND` config — **not** per-subscription |
| Notifications channel model | SFBL-117's `smtp` + `ses` channels collapse to a single `email` channel; backend is a deployment concern |
| Recipient model | Exactly one recipient per send. `EmailMessage.to: str`, no `cc`/`bcc`. One send → one `email_delivery` row. Multi-recipient is a future `send_many(...)` helper if a real use case appears. |
| Send lifecycle | Synchronous first attempt + background retries. `send()` awaits the first backend call and returns the post-attempt row. Callers that must not block wrap in `asyncio.create_task`. |
| Delivery log recipient storage | `to_hash` (sha256) + `to_domain` by default; plaintext opt-in via `EMAIL_LOG_RECIPIENTS=true` |
| Template engine | Jinja2 behind a thin `render()` wrapper in `templates.py` |
| Template layout | Trio of `subject.txt`, `body.txt`, `body.html` per template; base layout via inheritance; autoescape on for HTML |
| Subject safety | Layered validation — per-template `SUBJECT_CONTEXT` allowlist enforced at load time, auth templates require static subjects, post-render deny checks for URLs / opaque tokens / control chars / length, typed `EmailRenderError` on violation. No runtime `assert`. |
| Retry policy | Transient failures retried with capped exponential backoff + additive jitter up to `EMAIL_MAX_RETRIES`; permanent failures not retried |
| Retry claim model | Row-level CAS claim (`claimed_by`, `claim_expires_at`, `next_attempt_at`). Only the worker holding a live lease performs a retry; expired leases are reclaimable. |
| Error classification | Backend errors map to a fixed `EmailErrorReason` enum (9 values). Metric labels use the enum only; raw provider codes live in `last_error_msg` and structured logs. |
| Idempotency | Caller-supplied `idempotency_key`, enforced by unique DB constraint |
| Credential precedence | For SMTP password: env var wins if non-empty, file is fallback, missing value with `EMAIL_BACKEND=smtp` is a hard boot error. SES uses the boto3 default credential chain. |
| Desktop profile | `EMAIL_BACKEND=noop` by default — no network email from a desktop app |
| aws_hosted profile | `EMAIL_BACKEND=ses` by default |
| self_hosted profile | `EMAIL_BACKEND=noop` until admin explicitly configures SMTP |
| Template stubs | `auth/password_reset`, `auth/email_change_verify`, `notifications/run_complete` |

---

## Non-goals

- **No queue infrastructure.** In-process `asyncio` task with bounded retry is
  sufficient for current scale. The architecture must not block a future
  move to a durable queue (Celery/SQS/etc.), but that migration is not in
  scope here.
- **No multi-recipient sends in the primitive.** `EmailMessage.to` is a
  single string; `cc`/`bcc` are not supported. If a future consumer needs
  multiple recipients, the additive answer is a `send_many(messages)`
  helper that calls `send()` N times — not widening the primitive.
- **No cross-instance coordination beyond row-level CAS claims.** If two
  orchestrator instances race on the same pending row, exactly one wins
  the claim; the other skips. That is the extent of the multi-worker
  story for email.
- **No marketing / campaign features.** Single transactional send only.
- **No inbound email parsing.** SES bounce/complaint webhook handling is
  explicitly deferred to a later ticket.
- **No suppression list** beyond honouring per-send permanent failures in
  the delivery log.
- **No rich HTML editor.** Templates ship with the app and are reviewed via PR.
- **No per-user/per-tenant backend selection.** Backend is a deployment setting.

---

## Current state

| Layer | Status |
|---|---|
| Email sending | None — no SMTP or SES code exists in the backend |
| `User.email` field | Exists, currently unused |
| SMTP/SES dependencies | Not present in `requirements.txt` |
| Template engine | None — Jinja2 not yet a direct dep |
| Operator docs | No `docs/email.md` |

SFBL-117 and SFBL-119 both assume "SMTP/SES adapters from the Notifications
epic" today. Both need their descriptions amended once this spec lands, to
replace that scope with a dependency on the new epic (handled in
child ticket #8).

---

## Architecture

### Module layout

```
backend/app/services/email/
├── __init__.py            # re-exports EmailService, EmailMessage, EmailCategory,
│                          # EmailError, EmailRenderError, EmailErrorReason
├── service.py             # EmailService — public entrypoint
├── message.py             # EmailMessage dataclass + EmailCategory enum
├── errors.py              # EmailError, EmailRenderError, EmailErrorReason
├── templates.py           # Jinja2 env + render() + subject validation
├── delivery_log.py        # writes email_delivery rows; CAS claim/release helpers
├── backends/
│   ├── base.py            # EmailBackend Protocol + BackendResult
│   ├── smtp.py            # aiosmtplib-based + SMTP classify table
│   ├── ses.py             # aioboto3-based, SES v2 SendEmail + SES classify table
│   └── noop.py            # records status=skipped; dev/desktop/tests
└── templates/
    ├── base/
    │   ├── layout.html
    │   └── layout.txt
    ├── auth/
    │   ├── password_reset/
    │   │   ├── template.py       # REQUIRED_CONTEXT, SUBJECT_CONTEXT = frozenset()
    │   │   ├── subject.txt
    │   │   ├── body.txt
    │   │   └── body.html
    │   └── email_change_verify/
    │       └── ...
    └── notifications/
        └── run_complete/
            └── ...
```

Tests mirror this layout under `backend/tests/services/email/`.

### Public API

```python
class EmailCategory(str, Enum):
    NOTIFICATION = "notification"
    AUTH = "auth"
    SYSTEM = "system"


@dataclass(frozen=True)
class EmailMessage:
    to: str                             # exactly one recipient — enforced in __post_init__
    subject: str
    text_body: str
    html_body: str | None = None
    reply_to: str | None = None
    headers: dict[str, str] | None = None


class EmailService:
    async def send(
        self,
        message: EmailMessage,
        *,
        category: EmailCategory,
        idempotency_key: str | None = None,
    ) -> EmailDelivery:
        """Attempt delivery of `message` and return the post-first-attempt row.

        Awaits the first backend call. On success, the returned row has
        status='sent'. On permanent failure, status='failed'. On transient
        failure, status='pending' and a retry has been scheduled as a
        background task.

        Callers that must not block on SMTP/SES latency should wrap this
        in `asyncio.create_task(...)` at the call site.
        """

    async def send_template(
        self,
        *,
        template: str,                  # e.g. "auth/password_reset"
        to: str,
        context: dict[str, Any],
        category: EmailCategory,
        idempotency_key: str | None = None,
    ) -> EmailDelivery: ...
```

`category` is required and drives metric labels — auth mail is auditable
separately from notification mail. The return value is the `EmailDelivery`
row in its post-first-attempt state.

### Typed errors

```python
class EmailError(Exception):
    """Base class for all email-service failures."""


class EmailRenderError(EmailError):
    """Raised when a template is invalid, missing context, or produces an
    unsafe subject. The exception message is the stable `code` only — the
    offending value never enters the message, logs, or delivery log."""

    def __init__(self, code: str, *, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail   # safe description, never the offending value
        super().__init__(code)
```

Stable `code` values:

- `MISSING_REQUIRED_CONTEXT`
- `UNKNOWN_CONTEXT_KEY`
- `SUBJECT_REFERENCES_DISALLOWED_KEY`
- `SUBJECT_CONTAINS_URL`
- `SUBJECT_CONTAINS_OPAQUE_TOKEN`
- `SUBJECT_CONTAINS_CONTROL_CHARS`
- `SUBJECT_TOO_LONG`
- `AUTH_TEMPLATE_DYNAMIC_SUBJECT` (load-time)
- `TEMPLATE_UNAVAILABLE`

### Error classification enum

```python
class EmailErrorReason(str, Enum):
    TRANSIENT_NETWORK = "transient_network"
    TRANSIENT_TIMEOUT = "transient_timeout"
    TRANSIENT_PROVIDER_THROTTLED = "transient_provider_throttled"
    TRANSIENT_PROVIDER_UNAVAILABLE = "transient_provider_unavailable"
    PERMANENT_REJECT = "permanent_reject"
    PERMANENT_AUTH = "permanent_auth"
    PERMANENT_CONFIG = "permanent_config"
    PERMANENT_ADDRESS = "permanent_address"
    UNKNOWN = "unknown"
```

Nine values, fixed. Used as the `reason` label on metrics and as the
normalised value stored in `email_delivery.last_error_code`. Backends map
provider-specific codes to this enum via documented lookup tables
(see "Retry classification" below).

### Backend Protocol

```python
class BackendResult(TypedDict):
    provider_message_id: str | None
    accepted: bool
    reason: EmailErrorReason | None
    error_detail: str | None     # sanitised, may embed raw provider code
    transient: bool              # drives retry decision


class EmailBackend(Protocol):
    name: ClassVar[str]          # "smtp" | "ses" | "noop"

    async def send(self, message: EmailMessage) -> BackendResult: ...
    async def healthcheck(self) -> bool: ...
    def classify(self, exc_or_code: Any) -> tuple[EmailErrorReason, bool]: ...
```

Backends own their classification table. `EmailService` never inspects raw
provider exceptions or codes — it reads `BackendResult.reason` and
`BackendResult.transient` only.

### Sending flow

```
EmailService.send(msg, category, idempotency_key)
│
├─ 1. INSERT email_delivery row — status=pending, attempts=0,
│     claimed_by=$worker_id, claim_expires_at=now()+lease,
│     next_attempt_at=now(), idempotency_key=$key (UNIQUE).
│     If idempotency_key already exists, return the existing row
│     without sending.
│
├─ 2. Call backend.send(msg). This is the awaited "first attempt".
│
├─ 3. Based on BackendResult:
│      │
│      ├─ accepted=True →
│      │     UPDATE status='sent', sent_at=now(),
│      │     provider_message_id=..., claim_expires_at=now()
│      │     (release lease). Return row.
│      │
│      ├─ accepted=False, transient=False →
│      │     UPDATE status='failed', last_error_code=reason,
│      │     last_error_msg=error_detail, claim_expires_at=now().
│      │     Return row.
│      │
│      └─ accepted=False, transient=True →
│            If attempts+1 >= EMAIL_MAX_RETRIES:
│              UPDATE status='failed', ... (as above).
│            Else:
│              UPDATE status='pending', attempts=attempts+1,
│                     last_error_code=reason, last_error_msg=error_detail,
│                     next_attempt_at=now()+backoff,
│                     claim_expires_at=now()   # release lease
│              asyncio.create_task(_retry_loop(row.id))
│            Return row.
│
└─ 4. Emit events, metrics, span attributes throughout.
```

Retry tasks and the boot-sweep re-enter at step 2 via the CAS claim
described below.

### Retry classification

Classification tables per backend. Anything unmapped falls to `UNKNOWN`
with a warning log carrying the raw code (so it can be added to the
table in follow-up).

| Backend | Provider code → `EmailErrorReason` | Transient? |
|---|---|---|
| SMTP | `421`, `450`, `451`, `452` → `TRANSIENT_PROVIDER_UNAVAILABLE` | yes |
| SMTP | `ConnectionError`, `DNS` → `TRANSIENT_NETWORK` | yes |
| SMTP | `asyncio.TimeoutError` → `TRANSIENT_TIMEOUT` | yes |
| SMTP | `535` → `PERMANENT_AUTH` | no |
| SMTP | `550`, `551`, `553`, `554` → `PERMANENT_REJECT` | no |
| SMTP | malformed envelope → `PERMANENT_ADDRESS` | no |
| SES | `Throttling` → `TRANSIENT_PROVIDER_THROTTLED` | yes |
| SES | `ServiceUnavailable`, `InternalFailure` → `TRANSIENT_PROVIDER_UNAVAILABLE` | yes |
| SES | socket / timeout → `TRANSIENT_NETWORK` / `TRANSIENT_TIMEOUT` | yes |
| SES | `MessageRejected` → `PERMANENT_REJECT` | no |
| SES | `MailFromDomainNotVerified`, `ConfigurationSetDoesNotExist`, `AccountSendingPaused` → `PERMANENT_CONFIG` | no |
| SES | `AccessDenied` → `PERMANENT_AUTH` | no |
| SES | `InvalidParameterValue` on address → `PERMANENT_ADDRESS` | no |
| noop | (never) | n/a — always `accepted=True` with `reason=None` |

### Retry & recovery

**Worker identity.** Each running backend process computes a stable
worker ID on startup: `f"{hostname}:{pid}"`. This identity is written to
`email_delivery.claimed_by` when the worker holds a lease.

**Lease via CAS.** Retries and the boot-sweep claim rows with a
single `UPDATE ... RETURNING`:

```sql
UPDATE email_delivery
SET claimed_by = :worker_id,
    claim_expires_at = now() + (:lease_seconds * interval '1 second')
WHERE id = :id
  AND status = 'pending'
  AND (claim_expires_at IS NULL OR claim_expires_at < now())
  AND (next_attempt_at IS NULL OR next_attempt_at <= now())
RETURNING *;
```

Zero rows returned → another worker owns the lease, or the retry is not
yet due; this worker skips. One row returned → this worker owns the
lease and proceeds with the attempt.

The initial `send()` call writes the row already claimed by the sender
with a lease of `EMAIL_CLAIM_LEASE_SECONDS`, so no other worker can
pre-empt it during the first attempt.

**Backoff with additive jitter and cap.**

```python
delay = min(
    EMAIL_RETRY_BACKOFF_SECONDS * (2 ** attempt),
    EMAIL_RETRY_BACKOFF_MAX_SECONDS,
) + random.uniform(0, EMAIL_RETRY_BACKOFF_SECONDS)
next_attempt_at = now() + delay
```

Additive jitter breaks up thundering herds without pathologically long
waits. `next_attempt_at` is persisted so any worker (including one that
started after the scheduling worker exited) can pick up the retry when
the lease has expired.

**Boot-sweep.** Runs once at app startup after the DB connection is
established:

```sql
UPDATE email_delivery
SET status = 'failed',
    last_error_code = 'unknown',
    last_error_msg = 'Pending row exceeded EMAIL_PENDING_STALE_MINUTES'
WHERE status = 'pending'
  AND (claim_expires_at IS NULL
       OR claim_expires_at < now() - (:stale_minutes * interval '1 minute'));
```

The sweep only touches rows whose lease has been expired for at least
`EMAIL_PENDING_STALE_MINUTES` — so it cannot race an actively-leasing
worker. Rows reaped by the sweep emit `email.send.failed` with
`outcome_code=unknown` and are not retried.

**Contention signal.** When a retry task's CAS claim returns zero rows
(lease was held by another worker, or the row has already moved to a
terminal state), the task emits `email.send.claim_lost` with the row id
and increments `sfbl_email_claim_lost_total{backend}`. Non-zero
steady-state values here indicate either healthy multi-worker contention
or a bug — operators can distinguish by correlating with worker counts.

---

## Data model

### `email_delivery` (new table, Alembic migration in child ticket #2)

| Column | Type | Notes |
|---|---|---|
| `id` | `UUID` PK | |
| `created_at`, `updated_at` | timestamptz | |
| `category` | text, not null | `notification` \| `auth` \| `system` |
| `template` | text, nullable | `auth/password_reset` etc. Null for raw `send()`. |
| `to_hash` | text, not null | `sha256(recipient_lowercase)` hex digest. Singular — one recipient per row by contract. |
| `to_domain` | text, not null | e.g. `jenkin.org` — coarse debugging without PII |
| `to_addr` | text, nullable | **Populated only if `EMAIL_LOG_RECIPIENTS=true`** |
| `subject` | text, not null | Stored as-rendered; validated by the subject-safety layers before insert |
| `backend` | text, not null | `smtp` \| `ses` \| `noop` |
| `status` | text, not null | `pending` \| `sent` \| `failed` \| `skipped` |
| `attempts` | int, not null, default 0 | |
| `last_error_code` | text, nullable | Normalised `EmailErrorReason` enum value. Never a raw provider code. |
| `last_error_msg` | text, nullable | Sanitised via `safe_exc_message`; may prefix the raw provider code, e.g. `"[SES:Throttling] ..."` |
| `provider_message_id` | text, nullable | SES `MessageId` / SMTP `Message-Id` header value |
| `idempotency_key` | text, nullable, **unique** | Caller-supplied |
| `claimed_by` | text, nullable | Worker identity: `${hostname}:${pid}` |
| `claim_expires_at` | timestamptz, nullable | Lease expiry; row is owned until this time |
| `next_attempt_at` | timestamptz, nullable | Earliest time a retry is allowed to fire |
| `sent_at` | timestamptz, nullable | |

**Indexes:**

- `UNIQUE (idempotency_key)` — where not null
- `(status, next_attempt_at)` — for retry sweepers
- `(status, claim_expires_at)` — for boot-sweep

**What is deliberately NOT stored:** the rendered body (text or HTML), the
template context dict, any password-reset or email-change token, the
recipient address in cleartext unless explicitly opted in, any SMTP /
SES secret, or multiple recipients per row (not supported — one per row
by contract).

---

## Configuration

### New `Settings` fields on `backend/app/config.py`

```python
# Email — general
email_backend: Literal["smtp", "ses", "noop"] = "noop"
email_from_address: str | None = None
email_from_name: str | None = None
email_reply_to: str | None = None
email_max_retries: int = 3
email_retry_backoff_seconds: float = 2.0
email_retry_backoff_max_seconds: float = 120.0
email_timeout_seconds: float = 15.0
email_claim_lease_seconds: int = 60
email_pending_stale_minutes: int = 15
email_log_recipients: bool = False   # Opt-in plaintext recipient storage

# Email — SMTP
email_smtp_host: str | None = None
email_smtp_port: int = 587
email_smtp_username: str | None = None
email_smtp_password: str | None = None
email_smtp_password_file: str | None = None
email_smtp_starttls: bool = True
email_smtp_use_tls: bool = False     # implicit TLS (port 465)

# Email — SES
email_ses_region: str | None = None
email_ses_configuration_set: str | None = None
# SES credentials resolved via boto3 default chain — no explicit keys here
```

### SMTP password precedence (authoritative)

`EMAIL_SMTP_PASSWORD` env var is resolved in this order:

1. **If `EMAIL_SMTP_PASSWORD` env var is set and non-empty**, it is used as-is.
2. **Else if `EMAIL_SMTP_PASSWORD_FILE` is set and the file exists**, the file's
   contents (stripped) are used.
3. **Else**, the password is considered absent.

If the resolved password is absent **and** `EMAIL_BACKEND=smtp`, Settings
validation raises `ValueError` at boot — the app will not start. This is a
deliberate divergence from `ENCRYPTION_KEY` / `JWT_SECRET_KEY`, which
auto-generate on absence. An auto-generated SMTP password is useless to
the operator, so we fail loud instead.

This precedence is the **single source of truth**; any other mention of
SMTP password resolution in this doc defers to this paragraph.

### Distribution profile defaults

Added to `Settings._apply_distribution_profile`:

| Profile | Default `email_backend` | Rationale |
|---|---|---|
| `desktop` | `noop` | Desktop apps shouldn't send network email. |
| `self_hosted` | `noop` | Safe default — admin must explicitly configure SMTP. |
| `aws_hosted` | `ses` | Cloud-native path; IAM role provides credentials. |

### Validation rules

Enforced in a `model_validator`:

- If `email_backend == "smtp"`, then `email_smtp_host`, `email_from_address`,
  and the resolved SMTP password must all be set.
- If `email_backend == "ses"`, then `email_from_address` must be set.
  `email_ses_region` may fall back to AWS default region resolution.
- `email_from_address` must parse as a valid RFC-5321 address.
- `email_max_retries >= 0`, `email_retry_backoff_seconds > 0`,
  `email_retry_backoff_max_seconds >= email_retry_backoff_seconds`.
- `email_claim_lease_seconds > email_timeout_seconds` (a lease must cover
  one full send attempt).

---

## Templating

### Rules

Subjects are validated in **four layers**. No layer alone is sufficient;
each catches what the others miss.

#### Layer 1 — Per-template `SUBJECT_CONTEXT` allowlist (load time)

Each template directory contains a `template.py` declaring its
required context and the subset of keys its subject may reference:

```python
# templates/notifications/run_complete/template.py
REQUIRED_CONTEXT = {"plan_name", "run_id", "status", "total_rows",
                    "success_rows", "failed_rows", "started_at",
                    "ended_at", "run_url"}
SUBJECT_CONTEXT = {"plan_name", "status"}   # allowlist — strictly a subset
```

At app boot the template loader parses each `subject.txt` with the Jinja
AST (`Environment.parse` + `meta.find_undeclared_variables`) and diffs
the referenced variable set against `SUBJECT_CONTEXT`. Any reference
outside the allowlist is a hard load failure
(`EmailRenderError(SUBJECT_REFERENCES_DISALLOWED_KEY)`).

#### Layer 2 — Auth templates must have static subjects (load time)

Any template under `auth/**` MUST have `SUBJECT_CONTEXT = frozenset()` —
no interpolation in the subject at all. Verified at load time. Failure
raises `EmailRenderError(AUTH_TEMPLATE_DYNAMIC_SUBJECT)` and is fatal
to app startup.

This kills the entire leak class by construction for the highest-sensitivity
category. The reset email subject is a literal string like
`"Reset your Salesforce Bulk Loader password"` — no name, no URL, no
token can ever appear in it regardless of context contamination.

#### Layer 3 — Post-render deny check (render time, always on)

After a subject is rendered, an explicit runtime check runs — not an
`assert`, not gated on `__debug__`:

```python
def _validate_rendered_subject(rendered: str) -> None:
    if URL_RE.search(rendered):
        raise EmailRenderError("SUBJECT_CONTAINS_URL")
    if LONG_OPAQUE_TOKEN_RE.search(rendered):   # [A-Za-z0-9+/=_-]{24,}
        raise EmailRenderError("SUBJECT_CONTAINS_OPAQUE_TOKEN")
    if CONTROL_CHAR_RE.search(rendered):        # header-injection defence
        raise EmailRenderError("SUBJECT_CONTAINS_CONTROL_CHARS")
    if len(rendered) > 200:
        raise EmailRenderError("SUBJECT_TOO_LONG")
```

This is the last-line defence if Layer 1/2 somehow let something through
(misconfigured manifest, Jinja edge case, future template bug).

#### Layer 4 — Per-template pathological-context tests (test suite)

Every template ships with a property-style test that renders its subject
against a battery of pathological contexts — recipient display name
`"https://evil.example.com/?token=abc"`, unicode, nulls, 10 KB strings,
embedded newlines. The test asserts either clean validation or
`EmailRenderError` with the expected code. No template is accepted in
review without this test.

### Boot-time posture

- **Auth template with dynamic subject** → app refuses to start
  (`ValueError` raised during Settings / template load).
- **Non-auth template with an invalid manifest** → template is marked
  unavailable. `email.template.load_failed` event fires. Calls to
  `send_template(...)` against it raise
  `EmailRenderError(TEMPLATE_UNAVAILABLE)` at call time. The app still
  boots — degraded, not dead.

### Rendering and other rules

- `body.txt` and `body.html` may reference the full `REQUIRED_CONTEXT`.
  Subject allowlist applies to `subject.txt` only.
- `base/layout.html` and `base/layout.txt` provide outer chrome; template
  body files `{% extends %}` them.
- HTML bodies autoescape by default. Explicit `| safe` is allowed only
  in reviewed base layout.
- Missing required context keys raise
  `EmailRenderError(MISSING_REQUIRED_CONTEXT)`.
- Extra context keys beyond `REQUIRED_CONTEXT` raise
  `EmailRenderError(UNKNOWN_CONTEXT_KEY)` — prevents silent drift.

### Shipped templates (stubs)

| Template | REQUIRED_CONTEXT | SUBJECT_CONTEXT | Consumer |
|---|---|---|---|
| `auth/password_reset` | `user_display_name`, `reset_url`, `expires_in_minutes` | `∅` (static) | SFBL-119 |
| `auth/email_change_verify` | `user_display_name`, `confirm_url`, `new_email`, `expires_in_minutes` | `∅` (static) | SFBL-119 |
| `notifications/run_complete` | `plan_name`, `run_id`, `status`, `total_rows`, `success_rows`, `failed_rows`, `started_at`, `ended_at`, `run_url` | `{plan_name, status}` | SFBL-117 |

Consumer epics fill in final body copy; this epic ships minimal renderable
stubs so end-to-end tests can assert delivery.

---

## Security and telemetry hygiene

### Credentials

- SMTP password resolution: see "SMTP password precedence (authoritative)"
  above. That paragraph is the single source of truth.
- SES credentials come from the boto3 default chain: IAM role in
  `aws_hosted`, env or `~/.aws/credentials` in self-hosted. No SES keys
  in `config.py`.
- Secrets never appear in log records, span attributes, or error-monitoring
  events.

### Sanitisation updates

Add to `SCRUBBED_KEYS` in `app/observability/sanitization.py`:

- `email_smtp_password`
- `ses_secret_access_key`
- `aws_secret_access_key`
- `to`, `to_addr`, `recipient`, `recipients`
- `reset_url`, `confirm_url`, `token`

Backend error strings (`BackendResult.error_detail`) are sanitised before
persistence via `safe_exc_message` — SMTP servers occasionally echo
`RCPT TO:<addr>` lines in 5xx responses.

### Token handling

- Password reset tokens (SFBL-119 concern) are passed to `send_template` in
  the `context` dict only. They never enter the delivery log.
- Layer 1/2 of subject validation prevents tokens from ever reaching the
  subject; Layer 3 is the render-time belt-and-braces.
- Subjects of auth templates are fully static by construction, so no
  amount of context contamination can leak a token via the subject.

---

## Observability

Per `docs/observability.md` DoD, this ticket introduces a new execution
boundary and must add the following.

### Canonical events (new class `EmailEvent`)

Added to `app/observability/events.py`:

```
email.send.requested
email.send.succeeded
email.send.failed
email.send.retried
email.send.skipped
email.send.claim_lost
email.template.load_failed
```

### Outcome codes

Added to `OutcomeCode`:

```
EMAIL_SMTP_ERROR           = "email_smtp_error"
EMAIL_SES_ERROR            = "email_ses_error"
EMAIL_RENDER_ERROR         = "email_render_error"
EMAIL_CONFIG_ERROR         = "email_config_error"
EMAIL_TEMPLATE_LOAD_FAILED = "email_template_load_failed"
```

### Metrics

Added to `app/observability/metrics.py`:

```
sfbl_email_send_total{backend, category, status}             # counter
sfbl_email_send_duration_seconds{backend, category}          # histogram
sfbl_email_retry_total{backend, reason}                      # counter
sfbl_email_claim_lost_total{backend}                         # counter
```

Label contract:

- `backend` — `smtp` \| `ses` \| `noop` (3 values)
- `category` — `notification` \| `auth` \| `system` (3 values)
- `status` — `sent` \| `failed` \| `skipped` \| `pending` (4 values)
- `reason` — the `EmailErrorReason` enum (9 values)

**Cardinality ceiling.** Email metrics have a fixed upper bound of
`3 × 3 × 4 × 9 = 324` series. The `reason` label never accepts raw
provider codes — those live only in structured logs and
`email_delivery.last_error_msg`. Any future new backend, category, or
reason requires a spec update.

### Spans

`email.send` span around backend call with attributes:

- `email.backend` — `smtp|ses|noop`
- `email.category` — `notification|auth|system`
- `email.template` — e.g. `auth/password_reset` (nullable)
- `email.to_domain` — coarse recipient domain
- `email.attempt` — retry iteration index
- `email.reason` — `EmailErrorReason` value on failure (nullable)

Raw provider codes may appear on span attributes as
`email.provider_error_code` but never as a metric label.

### Health / readiness

`/dependencies` endpoint (in `app/api/utility.py`) adds an `email` entry
when `email_backend != "noop"`:

- SMTP: TCP connect to `(host, port)` with 2s timeout
- SES: `GetSendQuota` call with cached result (60s TTL)

Failure is `degraded`, not `unhealthy` — the app can still boot and serve
traffic without email.

---

## Operator surface

### Admin test-send

- `POST /api/admin/email/test` — body `{ "to": "...", "template": "..." }`.
  Requires local admin auth (present on hosted profiles; 404 on desktop).
- Frontend: **Settings → Email** panel showing:
  - current `email_backend`
  - dependency check status (green/yellow)
  - `FROM` address readout
  - "Send test" form (recipient + template picker)

### Docs

- `docs/email.md` — operator guide covering:
  - Configuring SMTP vs. SES (credentials, profile defaults).
  - SMTP password resolution order (with a cross-link to this spec).
  - How to read the delivery log, including `last_error_code`
    vs. `last_error_msg` (enum vs. raw-prefixed free-text).
  - What `EMAIL_LOG_RECIPIENTS` does and when to enable it.
  - Troubleshooting: common `EmailErrorReason` values and their causes.
  - Calling conventions for non-blocking sends
    (`asyncio.create_task` wrap pattern).

---

## Impact on downstream epics

### SFBL-117 — Run-Complete Notifications

Changes required (child ticket #8 of this epic):

- Description rewritten so "Channel adapters: `smtp_channel.py`,
  `ses_channel.py`" collapses to a single `email_channel.py` that delegates
  to `EmailService.send_template("notifications/run_complete", ...)`.
- `NotificationSubscription.channel` enum becomes `email` | `webhook`
  (not `smtp` | `ses` | `webhook`). Backend is a deployment setting.
- SMTP/SES config scope removed from SFBL-117; it depends on this epic.
- "Config + settings" child task becomes redundant — removed.
- `is blocked by` link to this epic.

### SFBL-119 — User Profile & Password Reset

Changes required (child ticket #8 of this epic):

- "Email delivery: reuse the SMTP / SES adapters built in the Notifications
  epic" becomes "Email delivery via
  `EmailService.send_template("auth/password_reset", ...)` from the
  Email Service epic".
- "Dependencies" section updated: replace the Notifications epic dep with
  this epic.
- `is blocked by` link to this epic.

---

## Child ticket breakdown

Implementation is split into 8 child Tasks. Each carries the observability
DoD checklist from `docs/observability.md` and the "Code Standards" rules
from `CLAUDE.md`. Architectural decisions go into `DECISIONS.md` as per
project convention — most relevant in #1 (credential precedence) and
#4 (SES configuration-set model).

| # | Ticket | Scope summary |
|---|---|---|
| 1 | Spec + distribution config | This doc + `docs/email.md` + `Settings` fields + profile defaults + SMTP password precedence validator + validators for backoff / lease sanity. No behaviour. |
| 2 | Core service + noop backend + delivery log | `EmailMessage` (single `to`), `EmailCategory`, `EmailError` / `EmailRenderError` / `EmailErrorReason`, `EmailService.send`, `NoopBackend`, `email_delivery` table + Alembic migration (with claim/lease columns and indexes), CAS claim helpers, boot-sweep. |
| 3 | SMTP backend | `aiosmtplib` impl, TLS modes, SMTP `classify` table, integration test against in-process `aiosmtpd`, transient/permanent retry tests, jitter-bounds test. |
| 4 | SES backend | `aioboto3` SES v2 impl, config-set tagging, credential chain, SES `classify` table, `moto`/`botocore` stub tests. |
| 5 | Template foundation + stub templates | Jinja2 env, `render()`, `template.py` manifest loading, AST-based `SUBJECT_CONTEXT` enforcement, auth-static-subject enforcement, post-render deny checks, per-template pathological tests, `send_template`. |
| 6 | Observability wiring | `EmailEvent`, new `OutcomeCode`s, metrics with fixed label cardinality, `email.send` span, `/dependencies` probe, sanitisation updates. |
| 7 | Admin test-send endpoint + UI | `POST /api/admin/email/test` + Settings → Email panel with dependency readout. |
| 8 | Downstream epic updates + blocker links | Rewrite SFBL-117 and SFBL-119 descriptions; create `is blocked by` links; comment on each. |

---

## Open questions

_None. All design questions were closed in the 2026-04-17 Q&A pass and
the subsequent defect-review pass (see revision log)._

---

## Revision log

| Date | Change |
|---|---|
| 2026-04-17 | Initial draft. |
| 2026-04-17 | Defect-review pass: locked recipient model to single recipient per send (`to: str`, no `cc`/`bcc`); locked send lifecycle to synchronous first attempt + background retries; replaced `assert`-based subject validation with layered `EmailRenderError` model (per-template `SUBJECT_CONTEXT` allowlist, static subjects for auth templates, post-render deny checks); defined retry claim model (CAS lease, jitter, capped backoff, boot-sweep) and added `claimed_by` / `claim_expires_at` / `next_attempt_at` columns; locked metric label cardinality to `EmailErrorReason` enum with fixed ceiling; clarified SMTP password precedence as env > file > hard error. |
