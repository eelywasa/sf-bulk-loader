---
title: Two-factor authentication
slug: two-factor-auth
nav_order: 115
tags: [2fa, totp, security, authentication, backup-codes]
summary: >-
  Enrol a TOTP authenticator, sign in with a code or backup code, rotate
  backup codes, and recover from a lost device.
---

# Two-factor authentication

## What this covers / who should read this

Available in hosted profiles (`self_hosted`, `aws_hosted`) for every signed-in
user — no permission is required to manage your own factor. The desktop
profile has no user accounts, so 2FA does not apply.

This page covers:

- Enrolling a TOTP authenticator from **Profile → Security**
- Signing in when 2FA is on (TOTP code or backup code)
- Regenerating your backup codes
- Disabling 2FA (only when the tenant does not require it)
- What happens if you lose your phone

Admins looking for the tenant-wide toggle or the break-glass path for another
user should see [Settings](settings.md) and [Account recovery](account-recovery.md).

---

## Enrolling

1. Open **Profile → Security**.
2. Click **Enable two-factor authentication**.
3. Scan the QR code with any TOTP authenticator app (Google Authenticator,
   1Password, Authy, Bitwarden, …). If scanning is not practical, expand
   **Can't scan?** and type the secret into the app manually.
4. Enter the 6-digit code the app produces and click **Verify**.
5. A list of **10 single-use backup codes** is displayed. **Download** or
   **Copy all** now — this is the only time they'll be shown.
6. Tick *"I've saved my backup codes somewhere safe"* and close the modal.

Your account is now protected. Subsequent logins will require a code.

### Where to store backup codes

Anywhere you'd store an emergency password — a password manager, a sealed
envelope in a drawer, a printed page kept with important documents. Do **not**
save them in the same place as your primary password (e.g. don't email them
to yourself). They are your only self-service recovery path if the
authenticator is lost.

---

## Signing in with 2FA on

1. Enter your email + password as usual.
2. You'll be routed to the **Two-factor verification** page.
3. Open your authenticator app and type the current 6-digit code.
4. Click **Verify**.

Codes rotate every 30 seconds. If you fat-finger one, you have a short
window to try again — after several failures the account falls back to the
standard login lockout path (see [Account recovery](account-recovery.md)).

### Using a backup code instead

On the verification page, click **Use a backup code instead**. Type the code
exactly as shown on your saved list — dashes are optional. Each backup code
works **once only** and is consumed on successful login.

You'll see a warning in the header once you drop below **two codes
remaining**. Regenerate a fresh set at that point.

---

## Regenerating backup codes

1. **Profile → Security → Regenerate backup codes**.
2. Enter a current TOTP code to authorise the rotation.
3. A new list of 10 codes is displayed. Save them the same way you saved the
   first set.
4. **All previous backup codes are invalidated immediately** — the old list
   no longer works.

Regenerate when: you used a backup code for sign-in, you're down to the last
couple, or you think the old list may have been copied elsewhere.

---

## Disabling 2FA

Only available when the **tenant has not made 2FA mandatory**. If the
"Disable" button is hidden or greyed out, the setting is enforced —
see [Settings](settings.md).

1. **Profile → Security → Disable two-factor authentication**.
2. Enter your **password** and a current **TOTP code**.
3. Click **Disable**. Your factor and all backup codes are removed.

---

## Lost your authenticator

You have two paths:

1. **Use a backup code to sign in**, then immediately regenerate a fresh
   factor from **Profile → Security** (disable → re-enable with the new
   authenticator). If the tenant requires 2FA, you'll still be able to
   disable and re-enrol in a single session since you have just verified.
2. **Ask an admin** to reset your 2FA factor — a row action under **Users →
   ⋯ → Reset 2FA**. After the reset you will be forced to enrol a fresh
   factor on your next sign-in.

If you have neither a backup code nor an admin available, an operator with
shell access to the backend can use the break-glass CLI — see
[Admin recovery](admin-recovery.md).

---

## What happens when the tenant requires 2FA

If an admin turns on **Require 2FA for all users** under
[Settings → Security](settings.md):

- Users **without** a factor are routed to a forced-enrolment wizard on
  their next sign-in — they cannot reach the app until they complete it.
- Users **with** a factor carry on as normal.
- Existing sessions remain valid — the enforcement applies on the next
  interactive sign-in, not to already-logged-in sessions.
- Self-service **Disable** is blocked while the toggle is on.

---

## Rate limits

Verification attempts are capped per user. Repeated failures surface a
"Too many attempts" response and, eventually, trigger the normal account
lockout path. Wait the stated window and retry — or, if this was not you,
regenerate your authenticator from Profile → Security, or ask an admin to
reset your factor.

---

## Related

- [Account recovery](account-recovery.md) — forgot password, locked out
- [User management](user-management.md) — admin reset row action
- [Settings](settings.md) — tenant-wide *Require 2FA* toggle
- [Admin recovery](admin-recovery.md) — break-glass CLI (`--keep-2fa`)
