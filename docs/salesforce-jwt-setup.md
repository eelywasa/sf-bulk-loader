# Salesforce JWT Bearer Flow: Setup Guide

This guide walks through the one-time Salesforce admin steps required before the
bulk loader can authenticate. The JWT Bearer flow is server-to-server — no browser
login, no refresh tokens, no interactive consent. Everything is pre-authorized by a
Salesforce admin.

---

## Overview

The flow needs three things in place:

1. An **RSA key pair** — you hold the private key; Salesforce holds the certificate.
2. A **Connected App** in the Salesforce org with the certificate uploaded.
3. The **running user pre-authorized** to use that Connected App without interactive consent.

---

## Step 1 — Generate an RSA Key Pair

Run these commands on any machine with OpenSSL (Linux, macOS, WSL, Git Bash on Windows):

```bash
# Generate a 2048-bit private key
openssl genrsa -out private.pem 2048

# Derive a self-signed X.509 certificate (what Salesforce needs)
openssl req -new -x509 -key private.pem -out certificate.crt -days 365
```

OpenSSL will prompt for certificate fields (`Country`, `Common Name`, etc.).  For a
self-signed cert used only for Salesforce JWT auth these values are cosmetic — fill
them in however you like.

**What you now have:**

| File | Goes to |
|------|---------|
| `private.pem` | Stays with you — pasted into the bulk loader UI |
| `certificate.crt` | Uploaded to Salesforce in Step 2 |

> **Key security:** `private.pem` is the credential. Store it like a password.  The
> bulk loader encrypts it at rest (Fernet + `ENCRYPTION_KEY`), but protect the source
> file and never commit it to version control.

To renew before expiry, repeat this step, upload the new `.crt` to the Connected App,
and update the private key stored in the bulk loader.

---

## Step 2 — Create a Connected App

1. In Salesforce Setup, search for **App Manager** and click **New Connected App**.

2. Fill in the **Basic Information** section:
   - **Connected App Name**: anything descriptive, e.g. `Bulk Loader`
   - **API Name**: auto-populated, leave it
   - **Contact Email**: your admin email

3. Under **API (Enable OAuth Settings)**:
   - Tick **Enable OAuth Settings**
   - **Callback URL**: `https://login.salesforce.com/services/oauth2/success`
     (required by the form but never used in the JWT flow)
   - Tick **Use digital signatures** and upload `certificate.crt`
   - **Selected OAuth Scopes** — add at minimum:
     - `Manage user data via APIs (api)`
     - `Perform requests at any time (refresh_token, offline_access)`

4. Click **Save**, then **Continue**.

5. On the app detail page, click **Manage Consumer Details**.  Copy the
   **Consumer Key** — this is the `client_id` value you will enter in the bulk loader.

> The Consumer Secret is not used in the JWT Bearer flow.

---

## Step 3 — Pre-authorize the User

This step is mandatory.  Without it Salesforce returns
`user hasn't approved this consumer` even with a valid JWT.

### 3a — Set Permitted Users policy

1. From the Connected App detail page, click **Manage** (top of page).
2. Click **Edit Policies**.
3. Under **OAuth Policies**, set **Permitted Users** to:
   **Admin approved users are pre-authorized**
4. Save.

### 3b — Grant access via Profile or Permission Set

The user that will appear in `sub` (the `username` field in the bulk loader) must be
explicitly granted access to the Connected App.

**Option A — via Profile:**
1. Still on the Connected App manage page, scroll to **Profiles**.
2. Click **Manage Profiles** → add the profile of the running user.

**Option B — via Permission Set (preferred for production):**
1. Create or open a Permission Set.
2. Under **Apps → Connected App Access**, add the Connected App.
3. Assign the Permission Set to the running user.

---

## Step 4 — Configure the Bulk Loader Connection

In the bulk loader UI (Connections → New Connection), enter:

| Field | Value |
|-------|-------|
| **Login URL** | `https://login.salesforce.com` (production) or `https://test.salesforce.com` (sandbox) |
| **Client ID** | Consumer Key from Step 2 |
| **Username** | The Salesforce username of the pre-authorized user |
| **Private Key** | Paste the full contents of `private.pem` (including the `-----BEGIN` / `-----END` lines) |
| **Sandbox** | Toggle on if using `test.salesforce.com` |

After saving, use **Test Connection** to verify end-to-end — the app will attempt a
JWT exchange and a lightweight API call.

---

## Troubleshooting

### `user hasn't approved this consumer`

The pre-authorization in Step 3 is missing or incomplete.  Verify:
- Permitted Users is set to **Admin approved users are pre-authorized**.
- The running user's Profile or a Permission Set with the Connected App is assigned.
- In sandboxes, check that the Permission Set assignment was made *in the sandbox*,
  not just in production.

### `invalid_client_id`

The Consumer Key (`client_id`) is wrong.  Re-copy it from **Manage Consumer Details**
— not the App Manager list view, which shows an older format in some orgs.

### `invalid_grant`

The JWT signature failed validation.  Likely causes:
- The wrong private key (doesn't match the uploaded certificate).
- The certificate has expired — regenerate the key pair and re-upload the `.crt`.
- A copy/paste issue with the PEM — ensure the full key including header/footer lines
  is present, with no extra whitespace at the start or end.

### `JWT expired`

The clock on the server running the bulk loader is significantly skewed.  The JWT has
a 3-minute lifetime.  Sync the system clock (`ntpdate`, Windows Time service, etc.).

### `INVALID_SESSION_ID` on API calls after a successful auth

The user doesn't have the API-enabled permission, or the `api` OAuth scope was not
added to the Connected App.  Check both.

---

## Certificate Renewal

Salesforce will stop accepting the JWT when the certificate expires (default 365 days
from creation).  To renew:

```bash
openssl genrsa -out private_new.pem 2048
openssl req -new -x509 -key private_new.pem -out certificate_new.crt -days 365
```

1. Upload `certificate_new.crt` to the Connected App (Edit → Use digital signatures →
   replace the file).
2. Update the private key in the bulk loader connection (edit the connection, paste
   `private_new.pem`).
3. Delete the old key files from disk.

> Consider setting a calendar reminder for ~30 days before expiry.
