---
title: Personal access tokens
slug: personal-access-tokens
nav_order: 112
tags: [tokens, api, authentication, programmatic]
required_permission: tokens.manage
summary: >-
  Create, use, and revoke personal access tokens for programmatic API access
  without exposing your account password.
---

# Personal access tokens

## What this covers / who should read this

Available in hosted profiles (`self_hosted`, `aws_hosted`) for every signed-in
user with the `tokens.manage` permission (admin and operator profiles by
default). The desktop profile (`auth_mode=none`) does not have user accounts,
so PATs do not apply.

This page covers:

- What a personal access token is and when to use one
- Creating a token (**copy-once** — shown in full only once)
- Using the token in API calls (`Authorization: Bearer sfbl_pat_…`)
- Listing your tokens to audit active access
- Revoking a token you no longer need
- Expiry and lifecycle

---

## What is a personal access token?

A personal access token (PAT) is a long-lived opaque credential you can use
instead of your session JWT when calling the Bulk Loader API from scripts, CI
pipelines, or external tooling. Unlike session tokens:

- **PATs do not expire automatically** (unless you set an expiry when you
  create them). A session JWT expires after the configured session lifetime
  (default 60 minutes); a PAT stays valid until you revoke it or it passes
  its `expires_at`.
- **PATs carry the same permission set as your user profile.** They do not
  grant any extra access.
- **A leaked PAT cannot mint new tokens or revoke existing ones.** Creating
  and revoking tokens requires session (cookie/JWT) authentication — PAT
  authentication is explicitly rejected on those endpoints.

Use a PAT when:
- You are calling the API from a script or CI job that cannot complete an
  interactive login.
- You want a credential that can be rotated independently of your password.
- You need a credential that survives JWT expiry across a long-running
  pipeline.

---

## Creating a token

> **Copy-once:** The full token value is shown **exactly once** — immediately
> after creation. It cannot be retrieved again. Store it in a secrets manager,
> a CI environment secret, or a similar secure store before closing the dialog.

1. Open **Settings → Personal access tokens** (or **Profile → Access tokens**,
   depending on your profile).
2. Click **New token**.
3. Enter a descriptive name (e.g. `nightly-etl` or `github-actions-staging`).
4. Optionally set an expiry date. Leave blank for a non-expiring token.
5. Click **Create**.
6. The full token value (beginning with `sfbl_pat_`) is displayed. **Copy it
   now** — this is the only time it will be shown.

The token list shows only the last 4 characters of each token so you can
identify which one to revoke, without exposing the full value.

---

## Using a token

Send the token as an `Authorization` header in every API request:

```
Authorization: Bearer sfbl_pat_<entropy>
```

Example with curl:

```bash
curl -s https://your-instance/api/connections/ \
  -H "Authorization: Bearer sfbl_pat_<your-token-here>"
```

Example in Python (httpx):

```python
import httpx

PAT = "sfbl_pat_<your-token-here>"
BASE_URL = "https://your-instance"

client = httpx.Client(headers={"Authorization": f"Bearer {PAT}"})
resp = client.get(f"{BASE_URL}/api/connections/")
resp.raise_for_status()
print(resp.json())
```

**PATs MUST be sent only via the `Authorization` header.** Passing a PAT as
a query parameter (e.g. `?token=…` or `?access_token=…`) is NOT supported
and will return `401 Unauthorized`. This is intentional — query parameters
can appear in server logs, browser history, and referrer headers, making them
unsuitable for credential transport.

---

## Listing your tokens

Open **Settings → Personal access tokens** to see all tokens you have created.
The list shows:

| Column | What it means |
|---|---|
| **Name** | The label you gave the token when you created it. |
| **Prefix / last 4** | `sfbl_pat_` prefix and the last 4 characters of the token. |
| **Created** | When the token was issued. |
| **Last used** | Approximate timestamp of the last authenticated API call (updated at most every 5 minutes). |
| **Expires** | Expiry date, or blank if the token does not expire. |
| **Status** | `Active`, `Revoked`, or `Expired`. |

Revoked tokens remain in the list so you can audit your token history.

---

## Revoking a token

1. Find the token you want to revoke in the list.
2. Click the **Revoke** (or **…** → Revoke) action.
3. Confirm the revocation.

Revoking a token is **immediate and irreversible**. Any in-flight request
that was already authenticated will complete; subsequent requests using that
token will receive `401 Unauthorized`.

Revoking a token requires session (cookie/JWT) authentication. A request
signed with a PAT cannot revoke tokens.

---

## Expiry

When you create a token with an expiry date, the backend enforces it on every
authenticated request. An expired token returns `401 Unauthorized` with the
generic "Invalid or expired token" message (same as a revoked token — the
backend intentionally does not distinguish between the two in error responses
to prevent token enumeration).

If a long-running job may span the expiry, either:
- Set an expiry far in the future, or
- Leave the expiry blank and rotate the token manually when your security
  policy requires it.

---

## Security notes

- Token values are **never logged** by the backend — only the last 4 characters
  and an opaque token ID appear in observability records.
- Tokens are stored as HMAC-SHA256 hashes derived from `ENCRYPTION_KEY` via
  HKDF — the plaintext is not recoverable from the database.
- Changing your password does NOT revoke your PATs. PATs are long-lived
  credentials intentionally decoupled from your interactive session watermark.
  If you suspect a token is compromised, revoke it explicitly.
- Account deactivation or suspension immediately blocks all PAT-authenticated
  requests for that account.

---

## Related

- [User management](user-management.md) — for admins managing user accounts
  and profiles
- [Settings reference](settings.md) — system-wide configuration
- [Two-factor authentication](two-factor-auth.md) — interactive session
  security
- [Architecture: Auth & RBAC](../architecture/auth-and-rbac.md) — PAT
  authentication flow and security model
