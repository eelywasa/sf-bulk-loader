---
title: Getting started
slug: getting-started
nav_order: 10
tags: [onboarding, profiles, authentication]
summary: >-
  What to do after the app is deployed — log in, understand which profile
  you are running, and create your first Salesforce connection.
---

# Getting started

## What this covers / who should read this

Everything you need to know after the Bulk Loader has been deployed and is
running. Read this first if you've just received a login, or if you are
deploying the app yourself and want to understand the first-boot admin path.
For deployment itself see the operator guides under
[`docs/deployment/`](../deployment/docker.md).

---

## Which profile am I on?

The app ships three distribution profiles. Each has different implications for
how you log in and what's available.

| Profile | Authentication | What's different |
|---|---|---|
| `desktop` | None — single-user Electron app | No login screen. No user management. No email notifications. |
| `self_hosted` | Email + password | Standard login. Users invited by an admin. |
| `aws_hosted` | Email + password (HTTPS enforced) | Same as self-hosted; cloud storage / RDS backing. |

The active profile is set at deployment time and cannot be changed from the UI.
You can see it on the login page footer (hosted profiles) or skip this section
entirely (desktop profile).

---

## First boot — the bootstrap admin (hosted profiles only)

On a fresh database with no users, the backend automatically creates the first
admin account from two environment variables:

- `ADMIN_EMAIL` — the login identifier
- `ADMIN_PASSWORD` — the initial password (must pass strength policy — ≥ 12
  characters, mixed case, digit, special)

These values are consumed **once**. After the first user exists, they are
ignored on every subsequent boot. If the variables are missing on an empty DB
the backend fails to start with a clear error pointing at the docs.

> **Two-admin rule.** As soon as possible, invite a second admin (see
> [User management](user-management.md)). The application refuses to disable,
> demote, or deactivate the last remaining admin — a safety net — but you
> should still have two so day-to-day admin work isn't tied to the break-glass
> account.

---

## Logging in (hosted profiles)

1. Visit the app URL in a browser. You will be redirected to `/login`.
2. Enter your email address and password.
3. On success you land on the dashboard.

If your account is **invited** but you have not set a password yet, use the
invitation link you received by email instead — see
[User management](user-management.md#inviting-a-user) for the mechanics.

### Forgotten password or locked out?

See [Account recovery](account-recovery.md).

---

## What to do next

1. **Create a Salesforce connection** — [Setting up a Salesforce connection](salesforce-connection.md).
2. **Prepare your CSV files** — [CSV format](csv-format.md).
3. **Author a load plan** — [Authoring load plans](load-plans.md).
4. **Run it** — [Running a load](running-loads.md).

---

## Related

- [Setting up a Salesforce connection](salesforce-connection.md)
- [User management](user-management.md) (admin)
- [Account recovery](account-recovery.md)
