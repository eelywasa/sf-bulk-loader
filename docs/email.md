# Email

Operator reference for the outbound email service. For the full design rationale
see [`docs/specs/email-service-spec.md`](specs/email-service-spec.md).

---

## Backend selection

The application routes all outbound email through a single, deployment-wide backend
selected by `EMAIL_BACKEND`. Three backends are available:

| Backend | Description |
|---------|-------------|
| `noop` | Records the delivery attempt as `skipped`; never opens a network connection. Safe default for desktop and unconfigured self-hosted deployments. |
| `smtp` | Delivers via STARTTLS (port 587, default) or implicit TLS (port 465). Works with any standards-compliant SMTP relay — Gmail app passwords, Mailgun SMTP, SES SMTP, Postfix, etc. |
| `ses` | Delivers via AWS SES v2 `SendEmail`. Credentials come from the boto3 default chain (IAM role, environment variables, `~/.aws/credentials`). Best choice for `aws_hosted` deployments. |

### Distribution-profile defaults

`EMAIL_BACKEND` is set automatically from the deployment profile when not explicitly
configured:

| `APP_DISTRIBUTION` | Default `EMAIL_BACKEND` | Why |
|---------------------|-------------------------|-----|
| `desktop` | `noop` | Desktop apps must not send network email by default. |
| `self_hosted` | `noop` | Safe fallback — admin must explicitly opt in to SMTP. |
| `aws_hosted` | `ses` | Cloud-native path; IAM role provides credentials automatically. |

Set `EMAIL_BACKEND` explicitly in `.env` to override the profile default.

### When to pick which backend

- **`noop`**: Development, local testing, or any deployment where email is not
  needed. No configuration required. Delivery rows are written with `status=skipped`.
- **`smtp`**: Self-hosted deployments using an external SMTP relay. Requires
  `EMAIL_SMTP_HOST`, `EMAIL_FROM_ADDRESS`, and a credential (see below).
- **`ses`**: AWS-hosted deployments. Requires a verified sender identity in SES
  and an IAM role with `ses:SendEmail` on the sending identity ARN. Set
  `EMAIL_FROM_ADDRESS` and optionally `EMAIL_SES_REGION` and
  `EMAIL_SES_CONFIGURATION_SET`.

---

## SMTP credential resolution

`EMAIL_SMTP_PASSWORD` is resolved in this order at application boot:

1. **`EMAIL_SMTP_PASSWORD` env var** — if set and non-empty, used as-is.
2. **`EMAIL_SMTP_PASSWORD_FILE`** — path to a file; if the file exists, its
   contents are read and stripped of leading/trailing whitespace.
3. **Neither present** — if `EMAIL_BACKEND=smtp`, the application refuses to
   start with a clear error message mentioning `EMAIL_SMTP_PASSWORD`. If the
   backend is `noop` or `ses`, the absence is silently accepted (password is
   irrelevant).

### Why no auto-generation

`ENCRYPTION_KEY` and `JWT_SECRET_KEY` can be auto-generated because they are
secrets the application owns end-to-end. SMTP passwords are credentials issued
by an external provider (SES SMTP, Gmail, Mailgun, Postfix). An auto-generated
secret is meaningless to that provider — the application would boot but every
send would fail with an auth error. Failing at boot surfaces the misconfiguration
immediately and unambiguously. See `DECISIONS.md` entry #019.

### How to rotate

To rotate the SMTP password without downtime:

1. Generate the new credential in your SMTP provider's dashboard.
2. Update `EMAIL_SMTP_PASSWORD` in `.env` (or write the new value to
   `EMAIL_SMTP_PASSWORD_FILE`).
3. Restart the backend container. The new credential is read at startup; no
   migration or DB change is required.

---

## Reading the delivery log

Every send attempt writes one row to the `email_delivery` table. Key columns:

| Column | Meaning |
|--------|---------|
| `status` | `pending` / `sent` / `failed` / `skipped` |
| `last_error_code` | Normalised `EmailErrorReason` enum value (see below). Never a raw provider code. |
| `last_error_msg` | Sanitised free-text. May be prefixed with the raw provider code, e.g. `"[SES:Throttling] ..."` or `"[SMTP:535] ..."`. |
| `attempts` | Number of send attempts made so far. |
| `backend` | Which backend handled this delivery (`smtp` / `ses` / `noop`). |
| `to_hash` | SHA-256 hex digest of the lowercased recipient address. |
| `to_domain` | Coarse recipient domain (e.g. `example.com`) — visible without enabling full recipient logging. |
| `to_addr` | Plaintext recipient. Populated only when `EMAIL_LOG_RECIPIENTS=true` (see below). |
| `provider_message_id` | SES `MessageId` or SMTP `Message-Id` header value on success. |

### `EmailErrorReason` values

`last_error_code` is always one of these nine values:

| Value | Meaning | Transient? | Troubleshooting |
|-------|---------|------------|-----------------|
| `transient_network` | TCP connection failed or DNS resolution error. | Yes | Check `EMAIL_SMTP_HOST` / SES endpoint reachability. Check network egress rules. |
| `transient_timeout` | Send attempt timed out (exceeded `EMAIL_TIMEOUT_SECONDS`). | Yes | Increase `EMAIL_TIMEOUT_SECONDS` or check provider latency. Consider a faster relay. |
| `transient_provider_throttled` | Provider rate-limited this send (e.g. SES `Throttling`). | Yes | Sending volume exceeds quota. Reduce send rate or request a quota increase from the provider. |
| `transient_provider_unavailable` | Provider returned a 4xx temporary failure (SMTP 421/450/451/452) or SES `ServiceUnavailable` / `InternalFailure`. | Yes | Usually self-resolving. If persistent, check the provider's status page. |
| `permanent_reject` | Provider rejected the message permanently (SMTP 550/551/553/554, SES `MessageRejected`). | No | Check sender domain/IP reputation. Verify SPF/DKIM/DMARC records. |
| `permanent_auth` | Authentication failed (SMTP 535, SES `AccessDenied`). | No | SMTP password wrong or expired; rotate via the procedure above. SES: check IAM policy has `ses:SendEmail`. |
| `permanent_config` | Provider-side configuration error (SES: `MailFromDomainNotVerified`, `ConfigurationSetDoesNotExist`, `AccountSendingPaused`). | No | Verify the sending identity in SES console. Check `EMAIL_SES_CONFIGURATION_SET` matches an existing config set. |
| `permanent_address` | Malformed or rejected envelope address (SMTP malformed envelope, SES `InvalidParameterValue` on address). | No | Check `EMAIL_FROM_ADDRESS` is a valid RFC-5321 address and is verified in SES. Check recipient address validity. |
| `unknown` | Error could not be mapped to any of the above categories. | Logged as warning | Check structured logs for the raw provider error code. File a ticket to add it to the classification table. |

Raw provider codes (e.g. `"Throttling"`, `"535"`) never appear in `last_error_code`
or as metric labels. They may appear in `last_error_msg` with a bracket prefix:
`"[SES:Throttling] Request rate exceeded"`. This makes them searchable in logs
without polluting metric cardinality.

---

## `EMAIL_LOG_RECIPIENTS` semantics

By default, recipient addresses are stored only as a SHA-256 hash (`to_hash`) and
domain (`to_domain`). This allows correlating delivery rows with a known recipient
address without persisting PII in cleartext.

Setting `EMAIL_LOG_RECIPIENTS=true` additionally populates `to_addr` with the
full recipient address in the delivery log.

**Privacy implications:**

- Any operator with read access to the database (or backups) can see all recipient
  addresses.
- If your deployment falls under GDPR, CCPA, or similar regulations, consider
  whether storing plaintext email addresses in the delivery log requires a
  data-processing basis and appropriate retention controls.
- The default (`false`) is deliberately privacy-preserving: `to_hash` is sufficient
  for debugging delivery issues against a known recipient, and `to_domain` gives a
  coarse signal without PII.

Enable plaintext logging only if your threat model and regulatory context allow it,
and you have a retention policy for the `email_delivery` table.

---

## Calling conventions for developers

`EmailService.send()` awaits the first backend call synchronously and returns the
post-attempt `EmailDelivery` row. Callers that must not block on SMTP or SES
network latency should wrap the call in `asyncio.create_task`:

```python
# Non-blocking fire-and-forget — do not await
asyncio.create_task(
    email_service.send(
        message,
        category=EmailCategory.NOTIFICATION,
        idempotency_key=f"run-complete:{run_id}",
    )
)
```

When the result is needed (e.g. to embed the delivery row ID in a response):

```python
# Awaited — blocks until the first attempt completes
delivery = await email_service.send(
    message,
    category=EmailCategory.AUTH,
    idempotency_key=f"password-reset:{token_id}",
)
if delivery.status == "failed":
    logger.warning("Password reset email failed", extra={"delivery_id": delivery.id})
```

The `idempotency_key` parameter is optional but recommended for any send that could
be retried by the caller (e.g. a user clicking "Resend" twice). Duplicate keys
return the existing row without re-sending.

Use `send_template()` for template-rendered emails:

```python
delivery = await email_service.send_template(
    template="auth/password_reset",
    to=user.email,
    context={
        "user_display_name": user.display_name,
        "reset_url": reset_url,
        "expires_in_minutes": 30,
    },
    category=EmailCategory.AUTH,
    idempotency_key=f"password-reset:{token_id}",
)
```

---

## Smoke testing email sends

Once email is configured, exercise it end-to-end via the admin test-send
endpoint at `POST /api/admin/email/test` (UI: **Settings → Email**).
The endpoint is admin-gated and returns 404 on the desktop profile, so
you must be logged in as admin on a `self_hosted` or `aws_hosted` build.

### 1. `noop` — wiring check (no setup, nothing sent)

No email leaves the box. Everything else (delivery row, event, metric,
`email.send` span) runs exactly as in production. Fastest way to verify
the service boots, the admin endpoint reaches it, and the delivery log
is being written.

```bash
# .env
EMAIL_BACKEND=noop
EMAIL_FROM_ADDRESS=smoke@test.local
```

Send from **Settings → Email**, then verify:

```bash
curl localhost:8000/api/health/dependencies | jq '.dependencies.email'
# → {"status":"ok","message":"email backend is noop; no external probe performed"}

curl localhost:8000/metrics | grep sfbl_email_send_total
# → sfbl_email_send_total{backend="noop",category="system",status="sent",...} 1

sqlite3 data/db/bulk_loader.db \
  "SELECT id,status,attempts,backend FROM email_delivery ORDER BY created_at DESC LIMIT 5"
```

### 2. `smtp` against a local catcher (realistic, safe)

Use [Mailpit](https://mailpit.axllent.org/) or MailHog — a fake SMTP server
with a web UI that shows captured mail. This is the right setup for
verifying template rendering, multipart bodies, headers, and retry
behaviour without risking real inboxes.

```bash
docker run -d --name mailpit -p 1025:1025 -p 8025:8025 axllent/mailpit
```

```bash
# .env
EMAIL_BACKEND=smtp
EMAIL_FROM_ADDRESS=smoke@test.local
EMAIL_SMTP_HOST=localhost
EMAIL_SMTP_PORT=1025
EMAIL_SMTP_USERNAME=            # Mailpit accepts blank
EMAIL_SMTP_PASSWORD=dummy       # required non-empty by the config validator
EMAIL_SMTP_STARTTLS=false       # Mailpit is plaintext by default
```

Restart the app, send from **Settings → Email**, then open
**http://localhost:8025** to inspect headers, raw source, and rendered
HTML of the captured message.

**Exercising the retry path**: stop Mailpit mid-send — the delivery row
stays `pending`, `attempts` increments, `sfbl_email_send_retry_total`
ticks. Restart Mailpit; the boot sweep (or the running retry loop)
completes delivery.

### 3. `ses` against real AWS (pre-production only)

```bash
# .env
EMAIL_BACKEND=ses
EMAIL_SES_REGION=us-east-1
EMAIL_FROM_ADDRESS=verified@yourdomain.com   # MUST be SES-verified in sandbox
```

Credentials resolve via the default boto3 chain (env, `~/.aws/credentials`,
or instance role).

SES sandbox gotchas:
- Both sender **and** recipient must be verified until the account is
  moved out of sandbox. Use your own verified address as `to` for smoke
  tests.
- Use a `+tag` on the address so you can filter bounce notifications
  and distinguish smoke-test traffic.
- Hitting `/api/health/dependencies` should return `email.status=ok`
  when creds + region are valid; flip the region to something invalid
  and re-probe to verify the `degraded` path.

### Coverage checklist beyond the happy path

Worth running through at least once per environment change:

| Scenario | How to trigger |
|---|---|
| Admin gating | Log in as a non-admin user → `POST /api/admin/email/test` should 403. Hit it on a desktop build → 404. |
| Idempotency | Send the same payload twice with the same `idempotency_key` → second call returns the first delivery's id, backend is not re-invoked. |
| `/dependencies` degrade | Point SMTP at a dead port (`EMAIL_SMTP_PORT=2`) and re-probe → `email.status=degraded`; overall `status` stays `ok` (email is non-critical). |
| Boot sweep | Send, kill the app mid-retry, restart → boot log includes an `email.boot_sweep_completed` event with a non-zero `reclaimed` count. |
| Template rendering | Not exposed via the admin UI (freeform only). Use a Python shell: `EmailService.send_template("auth/password_reset", {...}, to=..., category=EmailCategory.AUTH)`. |

The admin UI intentionally sends freeform messages only — template
rendering is driven by consumer epics (SFBL-117, SFBL-119) and the UI
will grow template-mode support there if it's needed for QA.

## Troubleshooting

### All sends failing with `permanent_auth`

**SMTP:** The configured password has been rotated at the provider but not updated
here. Rotate via the procedure in "How to rotate" above.

**SES:** The IAM role or user does not have `ses:SendEmail` on the sending identity
ARN. Check the role's policy in the AWS console. The error message in
`last_error_msg` will include `AccessDenied`.

### Sends succeeding but messages not arriving

Check `provider_message_id` — if populated, the provider accepted the message and
it left this system cleanly. The issue is downstream (spam filter, recipient MX
failure, bounce). Query SES or your SMTP relay's sending logs with the
`provider_message_id` to trace the message.

### High rate of `transient_provider_throttled`

Your sending volume exceeds the provider's quota. Options:

- Reduce the rate by adding delays between sends.
- Request a quota increase (SES: "Sending limits increase" in Service Quotas).
- Switch to a relay with higher throughput.
- Spread sends over a longer window using `next_attempt_at` backoff (handled
  automatically by the retry loop).

### `unknown` errors accumulating

An SMTP or SES error code is not in the classification table. Check structured logs
for the raw provider code (look for `[SMTP:NNN]` or `[SES:ErrorCode]` prefixes in
`last_error_msg`). File a ticket to add the code to the appropriate backend's
classification table so future occurrences get a meaningful `last_error_code`.

### App refuses to start with `EMAIL_SMTP_PASSWORD` error

`EMAIL_BACKEND=smtp` is set but no password is resolvable. Either:

- Set `EMAIL_SMTP_PASSWORD=<your-password>` in `.env`, or
- Set `EMAIL_SMTP_PASSWORD_FILE=/path/to/file` and ensure the file exists and
  is readable by the backend process.

See "SMTP credential resolution" above for the full precedence rules.

### Checking email backend health via `/api/health/dependencies`

The `/api/health/dependencies` endpoint includes an `email` entry that probes
the configured backend on every request:

```json
{
  "status": "ok",
  "dependencies": {
    "database": { "status": "ok" },
    "email":    { "status": "ok" }
  }
}
```

**Status values:**

| Value | Meaning |
|---|---|
| `ok` | Backend reachable (or `noop` — no probe performed) |
| `degraded` | Backend probe failed; email delivery may be impaired |

A `degraded` email status does **not** cause the overall status to be `failed`
and does **not** return HTTP 503. Email is non-critical for app functionality.
Only a `failed` status on a hard dependency (e.g. the database) triggers 503.

**What the probe does:**

- `noop` backend: always reports `ok` with a note; no network connection made.
- `smtp` backend: opens a TCP connection to the configured SMTP host on the
  configured port (2-second timeout). Does not authenticate or send.
- `ses` backend: calls `GetSendQuota` via the boto3 default credential chain
  (result is cached to avoid rate limits on repeated calls).

If email is `degraded`, check:
1. That the SMTP host/port is reachable from the backend container.
2. That the SES IAM role has `ses:GetSendQuota` permission.
3. Structured logs for the `email.send` event category and `health.checked`
   event near the probe time for detailed error messages.
