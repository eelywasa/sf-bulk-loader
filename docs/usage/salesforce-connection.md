---
title: Setting up a Salesforce connection
slug: salesforce-connection
nav_order: 20
tags: [connections, salesforce, jwt]
required_permission: connections.manage
summary: >-
  Create the Connected App in Salesforce, generate a key pair, and add the
  connection in the Bulk Loader UI.
---

# Setting up a Salesforce connection

## What this covers / who should read this

How to connect the Bulk Loader to a Salesforce org using the OAuth 2.0 JWT
Bearer flow. Read this before authoring your first load plan. Requires the
`connections.manage` permission in the Bulk Loader and System Administrator
access (or equivalent) in the Salesforce org you are connecting to. For the
full walkthrough with screenshots see
[Salesforce JWT setup](salesforce-jwt-setup.md).

---

## How the auth works

JWT Bearer is server-to-server — no browser login, no interactive consent, no
refresh tokens. Everything is pre-authorized by a Salesforce admin at setup
time. The Bulk Loader signs a short-lived JWT with a private key it holds;
Salesforce verifies the signature using the public certificate you uploaded
and issues an access token.

You need three things in place:

1. An **RSA key pair** — you hold the private key; Salesforce holds the
   certificate.
2. A **Connected App** in the Salesforce org with the certificate uploaded.
3. The **running user pre-authorized** to use that Connected App.

---

## Step 1 — Generate an RSA key pair

On any machine with OpenSSL (Linux, macOS, WSL, or Git Bash on Windows):

```bash
# Generate a 2048-bit private key
openssl genrsa -out server.key 2048

# Derive a self-signed certificate (what Salesforce needs)
openssl req -new -x509 -key server.key -out server.crt -days 365 \
  -subj "/CN=sf-bulk-loader"
```

Keep `server.key` secure — it's a long-lived credential. You'll paste its
contents into the Bulk Loader UI in Step 4. Never commit it to version
control.

---

## Step 2 — Create the Connected App in Salesforce

1. In Setup, search for **App Manager** → **New Connected App**.
2. Under **API (Enable OAuth Settings)**:
   - Enable OAuth Settings: **checked**
   - Callback URL: `https://localhost` (unused, but required by the form)
   - OAuth Scopes: `api`, `refresh_token`
   - Use digital signatures: **checked** — upload `server.crt` from Step 1
3. Save, then note the **Consumer Key** from the Connected App detail page —
   you'll paste this into the UI as **Client ID**.

---

## Step 3 — Pre-authorize the running user

1. **Setup → Manage Connected Apps → Policies**.
2. Set **Permitted Users** to *"Admin approved users are pre-authorized"*.
3. Add the Salesforce user that will execute the loads (Profile or Permission
   Set).

Without this step the JWT exchange will fail with `invalid_grant`.

---

## Step 4 — Add the connection in the Bulk Loader

1. In the UI, navigate to **Connections → Add Salesforce connection**.
2. Fill in:
   - **Login URL**: `https://login.salesforce.com` for production,
     `https://test.salesforce.com` for sandboxes.
   - **Client ID**: Consumer Key from Step 2.
   - **Username**: the Salesforce user pre-authorized in Step 3.
   - **Private Key**: full contents of `server.key` including the
     `-----BEGIN PRIVATE KEY-----` / `-----END PRIVATE KEY-----` headers.
3. Click **Save**.
4. Click **Test Connection** — the UI will do a real JWT exchange and confirm
   success or surface the Salesforce error.

Private keys are Fernet-encrypted at rest; the plaintext never leaves the
backend service.

---

## Troubleshooting

### `invalid_grant` on Test Connection

- **Consumer Key typo** — must match the Connected App exactly (no trailing
  whitespace).
- **User not pre-authorized** — confirm the Profile or Permission Set was
  added under *Manage Connected Apps → Policies*.
- **Sandbox vs production** — sandboxes must use `https://test.salesforce.com`.
- **Clock skew** — JWT `exp` claims are time-sensitive. Make sure the server
  clock is within a few seconds of reality.
- **Wrong certificate** — the public cert uploaded to Salesforce must match
  the private key stored in the connection.

### `user hasn't approved this consumer`

The running user is not pre-authorized. Repeat Step 3.

### `invalid_client_id`

The Consumer Key doesn't match an active Connected App in the org. Double-check
you pasted the value from the correct Connected App.

---

## Related

- [Authoring load plans](load-plans.md) (next step)
- [CSV format](csv-format.md)
- Architecture: [Salesforce auth flow](../architecture/run-execution.md#salesforce-auth)
- [Full Salesforce JWT setup walkthrough](salesforce-jwt-setup.md)
