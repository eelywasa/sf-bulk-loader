---
title: Account recovery
slug: account-recovery
nav_order: 120
required_permission: users.manage
tags: [password, recovery, profile]
summary: >-
  Update your profile, change your password, and recover from a locked or
  forgotten-password state (hosted profiles only).
---

# Account recovery

## What this covers / who should read this

Per-user self-service flows — updating your display name and email, changing
your password, and recovering access when you've forgotten the password or
been locked out. Available in hosted profiles (`self_hosted`, `aws_hosted`);
the desktop profile has no user accounts.

If you're an admin trying to recover the *system* (zero admins remaining,
app won't start), see [admin recovery](../admin-recovery.md) instead.

---

## Update your display name

1. Click your avatar / name in the bottom-left of the sidebar.
2. Open **Profile**.
3. Edit **Display name** → **Save**.

The change is immediate across the UI.

---

## Update your email address

Your email is used for password resets, invitation acceptance, and
notifications — so changing it is a two-step verification.

1. Open **Profile**.
2. Enter the new address and click **Request change**.
3. A verification link is sent to the **new** address. A change-notice email
   is sent to your **current** address so you're aware of the request.
4. Click the link in the verification email to confirm.
5. Links expire after a short window — request a new one if it has lapsed.

---

## Change your password

1. Open **Profile**.
2. Enter your **Current password**, then your **New password** twice.
3. Click **Change password**.

A new session token is issued immediately — any other open tabs need to log
in again. This is intentional.

### Password requirements

At least 12 characters, with a mix of upper-case, lower-case, digits, and
special characters.

---

## Forgot your password

1. On the login page, click **Forgot password?**
2. Enter your account email and click **Send reset link**.
3. Check your inbox for the reset email.
4. Click the link and set a new password.

The response on the form is **the same whether the email is registered or
not** — this is deliberate to prevent account enumeration. If you don't
receive the email within a minute or two, check spam and confirm the address
you used matches the one on your account.

Reset links expire after a short window and can be used only once.

---

## Locked out (too many failed logins)

Repeated failed logins lock the account. You have two paths back in:

1. **Wait** — the lock auto-clears after a configured timeout.
2. **Ask an admin** to unlock you from the Users page, or via the
   `admin-unlock` CLI (see [admin recovery](../admin-recovery.md)).

---

## Rate limiting

Password-reset and email-change requests are rate-limited. A "Too many
requests" error means wait a short time before trying again. The limits
exist to prevent abuse of the reset flow.

---

## Related

- [User management](user-management.md) — admin side of the same flows
- [Admin recovery](../admin-recovery.md) — break-glass CLI tools
- [Getting started](getting-started.md)
