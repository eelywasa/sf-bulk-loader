---
title: Settings
slug: settings
nav_order: 110
tags: [settings, configuration]
required_permission: system.settings
summary: >-
  System-wide settings — email, Salesforce defaults, partitioning,
  notifications.
---

# Settings

## What this covers / who should read this

The **Settings** pages configure system-wide behaviour. Requires
`system.settings` (admin-only by default). Per-user preferences (display
name, password, email) live under **Profile** instead — see
[Account recovery](account-recovery.md) and the *Profile & Password* flows
documented there.

---

## Settings pages

| Page | What it controls |
|---|---|
| **General** | App display name, default time zone. |
| **Salesforce** | Default API version (`SF_API_VERSION`), polling intervals for Bulk API jobs. |
| **Partitioning** | Default CSV partition size (per-step override still available on load plans). |
| **Notifications** | Your own notification subscriptions (all users), plus SMTP connectivity test for admins. |
| **Security** | Session lifetime, password policy hints, tenant-wide **Require 2FA** toggle. |

Not every setting is editable at runtime — some (encryption key, distribution
profile) are env-var only and shown here as read-only for reference.

---

## Environment variables vs UI settings

As a rule:

- **Secrets and infrastructure bindings** (`ENCRYPTION_KEY`, `DATABASE_URL`,
  SMTP credentials, `ADMIN_EMAIL`, `APP_DISTRIBUTION`) are env-var only —
  set at deploy time, not editable in the UI.
- **Operational knobs** (partition size, polling intervals, default
  connection) are UI-editable and stored in the database.

See [Docker deployment](../deployment/docker.md) for the full env-var
reference.

---

## Salesforce defaults

- **API version** — defaults to `v62.0`. Bumping here affects new runs; live
  runs complete on the version they started with.
- **Polling interval (initial / max)** — exponential backoff parameters when
  polling Bulk API job status. Defaults: 5s initial, 30s max.

Connection-level overrides are not currently exposed — all connections use
the global setting.

---

## Partitioning

- **Default partition size** — records per Bulk API job (default 10 000).
- **Cap** — Salesforce enforces a 150 MB per-upload limit; the loader
  caps below this regardless of the configured partition size.

Per-step overrides on load plans always win over the global default.

---

## Require 2FA for all users

**Settings → Security → Require 2FA** enforces two-factor authentication
across the tenant. When enabled:

- Users who already have a TOTP factor carry on as normal.
- Users **without** a factor are routed to a forced-enrolment wizard the
  next time they sign in — they cannot reach the app until they complete
  it. The short-lived MFA token issued at login is only valid for the
  enrolment endpoints.
- Users can no longer self-disable their factor (**Profile → Security →
  Disable** is hidden). They must ask an admin to use **Users → ⋯ →
  Reset 2FA**, or use the break-glass CLI, to clear a factor.
- Existing sessions remain valid — enforcement applies on the next
  interactive sign-in, not to tokens already in flight.

Turning the toggle **off** is non-disruptive: enrolled users keep their
factor, and self-service Disable becomes available again.

See [Two-factor authentication](two-factor-auth.md) for the user-side
flow and [User management](user-management.md) for the admin row action.

---

## Testing SMTP

**Settings → Notifications → Test email backend** sends a synthetic email
via the configured SMTP backend to your own account. Useful after changing
`SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` env vars to confirm the new
credentials work before someone relies on a run-completion notification.

---

## Related

- [User management](user-management.md)
- [Two-factor authentication](two-factor-auth.md) — user-side 2FA flow
- [Notifications](notifications.md)
- Deployment: [Docker env vars](../deployment/docker.md)
- [Admin recovery](admin-recovery.md)
