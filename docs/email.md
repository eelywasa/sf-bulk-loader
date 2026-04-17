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
