---
title: User management
slug: user-management
nav_order: 100
tags: [users, invitations, profiles, rbac]
required_permission: users.manage
summary: >-
  Invite users, assign profiles, and manage account lifecycle (hosted profiles
  only).
---

# User management

## What this covers / who should read this

How admins invite teammates, assign RBAC profiles, and handle the account
lifecycle (active / disabled / locked). Hosted profiles only — desktop has
no user accounts. Requires `users.manage`.

---

## Profiles (roles)

| Profile | Typical user | Permissions |
|---|---|---|
| `admin` | Platform owners | Everything — including `users.manage` and `system.settings`. |
| `operator` | Data engineers running loads | Execute runs, abort, view everything; manage plans + connections. |
| `viewer` | Stakeholders watching progress | Read-only — can see plans, runs, and file *metadata* but **not** file contents. |

A user has exactly one profile. Switching profiles is immediate — no
re-login required.

---

## Inviting a user

1. **Users → Invite user**.
2. Enter the invitee's email address and choose a **Profile**.
3. Click **Send invitation** — an email is dispatched containing a signed
   invitation link.

The link expires after `INVITATION_TTL_HOURS` (default 72). Expired
invitations can be **resent** from the user row, which issues a fresh token.

The invitee clicks the link, sets a password, and lands logged-in on the
dashboard. No password is ever chosen by the admin.

---

## User statuses

| Status | Meaning | Can log in? |
|---|---|---|
| `invited` | Invitation sent, not yet accepted. | No — only the invitation link works. |
| `active` | Accepted and usable. | Yes. |
| `disabled` | Admin-disabled. | No. |
| `locked` | Auto-locked after repeated failed logins. | No — needs admin unlock or timeout. |

Admins toggle **Disable** / **Enable** and **Unlock** from the Users page.
Unlocking is also available via the `admin-unlock` CLI — see
[admin recovery](../admin-recovery.md).

---

## Changing a user's profile

Edit the row on the Users page and pick a new profile. The change takes
effect on the user's next request.

**Do not** switch your own profile away from `admin` if you're the last
admin — the UI blocks this (the anti-bricking safeguard). The same rule
covers disabling the last active admin.

---

## Two-admin rule

The app enforces that **at least one admin** is always active. Any action
that would leave zero active admins is rejected with a clear error message:

- Switching the last admin's profile.
- Disabling the last active admin.
- Deleting (if deletion is exposed) the last admin.

Rationale: a zero-admin system is unrecoverable from the UI — it requires
the `admin-recover` CLI, which means shell access to the container. Prevent
the situation rather than forcing the recovery flow.

---

## Removing users

Hard-deleting users isn't currently exposed in the UI (it would orphan
historical audit rows). Use **Disable** to revoke access while keeping the
audit trail intact.

---

## Related

- [Getting started](getting-started.md) — bootstrap admin + first login
- [Account recovery](account-recovery.md) — forgotten passwords, lockouts
- [Admin recovery](../admin-recovery.md) — break-glass CLI tools
- Spec: [RBAC permission matrix](../specs/rbac-permission-matrix.md)
