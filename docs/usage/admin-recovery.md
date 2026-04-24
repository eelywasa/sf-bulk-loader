---
title: Admin recovery
slug: admin-recovery
nav_order: 115
tags: [admin, security, cli]
required_permission: users.manage
summary: >-
  Break-glass CLI for recovering admin access when no admin can log in through
  the UI — forgotten password, lockouts, missing email backend, fresh DB.
---

# Admin recovery — regaining access when locked out

## What this covers / who should read this

The operator procedure for regaining admin access to the Bulk Loader when no
admin can log in through the UI — forgotten password, locked-out account,
missing / unreachable email backend, or a fresh database where the bootstrap
admin has already been consumed. Read this before escalating to "restore from
backup" — in almost every case the break-glass CLI shipped with the backend is
the right tool.

---

## Prerequisites

You need shell access to the process that runs the backend. The CLI reads the
same database and environment as the running server, so `DATABASE_URL` and
`ENCRYPTION_KEY` must resolve the same way they do for the live app.

| Deployment | How to reach the CLI |
|---|---|
| Docker / Docker Compose | `docker compose exec backend bash`, or one-shot `docker compose exec backend python -m app.cli <subcommand>` |
| Desktop (Electron, packaged) | The packaged binary exposes the same subcommands — run `sf_bulk_loader --help` from the OS terminal |
| AWS (ECS / Fargate) | `aws ecs execute-command` into a running task, then `python -m app.cli <subcommand>` |

---

## Identity model in one paragraph

Users are identified by **email** (`User.email`). Authorization is
profile-based: every user has a `profile_id`
pointing at one of `admin`, `operator`, or `viewer`. There is no
`is_admin` column — "being an admin" means *the user's profile is the admin
profile*. The CLI works against this model directly.

---

## Step 1 — List existing admin accounts

```bash
python -m app.cli list-admins
```

Prints every user whose profile is `admin`, along with their status and
whether they are currently locked (either `status = locked` or
`locked_until` is a future timestamp).

Use this before any recovery step so you know which email addresses are
candidates.

---

## Step 2 — Recover a known admin account

If you know the email address of an admin user — even one that is locked or
whose password is forgotten:

```bash
python -m app.cli admin-recover admin@example.com
```

The command:

1. Generates a random temporary password and prints it **once** to stdout.
2. Sets `must_reset_password = true` so the user is forced to change it on
   first login.
3. Clears `locked_until` and `failed_login_count`.
4. Transitions `status` to `active` (from `invited`, `locked`, or
   `deactivated`).
5. Stamps `password_changed_at` so any stale JWTs the user might still hold
   are invalidated.
6. **Clears the user's 2FA factor + backup codes** (default). Pass
   `--keep-2fa` to preserve them — see *2FA reset behaviour* below.
7. Emits a WARNING-level audit log entry (`event_name=auth.admin.recovered`,
   `outcome_code=cli_recovery`) and writes a `login_attempt` row for the
   audit trail. When 2FA is cleared, an additional `mfa.admin_reset` event
   is emitted.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Success |
| 2 | No user found for the given email |
| 3 | User is not in the `admin` profile |
| 4 | User has `status = deleted` (soft-deleted — create a new account instead) |

Store the printed password securely and hand it to the user through a
trusted channel. It is not retrievable after the command exits.

### 2FA reset behaviour

By default `admin-recover` also clears the user's TOTP factor and all
backup codes. This matches the typical break-glass scenario — the admin has
lost access *and* their authenticator. On next sign-in the user will be
forced to enrol a fresh factor (if the tenant requires 2FA) or can choose
whether to re-enrol from **Profile → Security**.

If you're only resetting the password and want to preserve the existing
authenticator — for example, the user remembers their TOTP codes but forgot
their password — pass `--keep-2fa`:

```bash
python -m app.cli admin-recover admin@example.com --keep-2fa
```

With `--keep-2fa`, TOTP secret and backup codes are untouched and the user
will be challenged for a code on next sign-in as normal.

See [Two-factor authentication](two-factor-auth.md) for the user-side flow.

---

## Step 3 — Clear a lockout without resetting the password

If an admin account is locked (or a non-admin user is locked under the
progressive lockout policy) but the password is still known:

```bash
python -m app.cli unlock user@example.com
```

Sets `locked_until = NULL`, resets `failed_login_count = 0`, and transitions
`status` from `locked` back to `active` if applicable. Works on users of any
profile, not just admins. Exits with 2 if the user is not found.

---

## Step 4 — Bootstrap on a fresh database

On a brand-new database with no users, the backend's lifespan hook calls
`seed_admin()`, which creates the first admin from the environment:

| Variable | Purpose |
|---|---|
| `ADMIN_EMAIL` | Login identifier for the seeded admin (required) |
| `ADMIN_PASSWORD` | Initial password (required; must pass strength policy — ≥ 12 chars, mixed case, digit, special) |

Once a user exists, these values are ignored on subsequent boots. If the
env vars are missing on an empty DB the backend fails startup fast with
guidance pointing here.

In `auth_mode=none` (desktop profile) `seed_admin()` is skipped entirely —
the desktop app has no login surface, so no admin exists.

---

## Keep at least two active admins

The `/admin/users` page shows a warning banner when there is only one active
admin. Promote a second user to the admin profile to dismiss it.

Recommended setup:

1. **Bootstrap admin** (seeded from `ADMIN_EMAIL`) — break-glass, not used
   day-to-day.
2. **At least one operational admin** tied to a real person's email — used
   for actual administration.

With two admins, if one is locked out the other can reset their password
through the UI without needing shell access. The "last active admin cannot
be disabled, deactivated, or demoted" safeguard prevents the operational
admin from accidentally locking the whole org out.

---

## Security implications

**Anyone with shell access to the backend process can recover any admin
account.** This is intentional — it is the last resort when no other path is
available.

Practical controls:

- Restrict shell access to the backend process to authorised operators
  only (IAM, SSH certificates, `aws ecs execute-command` IAM policy, etc.).
- Review the WARNING log line and `login_attempt` row that `admin-recover`
  produces after each recovery — they are the audit trail.
- Rotate the temporary password immediately after handing it to the user.

---

## Related

- [User management](user-management.md) — day-to-day admin flows
- [Account recovery](account-recovery.md) — end-user password reset
- [Two-factor authentication](two-factor-auth.md) — enrol, sign in, recovery paths
- [Getting started](getting-started.md) — bootstrap admin on first run
