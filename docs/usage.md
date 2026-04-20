# Using the Salesforce Bulk Loader

---

## Salesforce Connected App Setup

The application authenticates to Salesforce using the **OAuth 2.0 JWT Bearer** flow
(server-to-server, no interactive login). This requires a Connected App in your org.

For a detailed walkthrough see [`docs/salesforce-jwt-setup.md`](salesforce-jwt-setup.md).

### Step 1: Generate an RSA key pair

```bash
# Generate private key (2048-bit RSA)
openssl genrsa -out server.key 2048

# Extract the public certificate
openssl req -new -x509 -key server.key -out server.crt -days 365 \
  -subj "/CN=sf-bulk-loader"
```

Keep `server.key` secure. You will paste its contents into the application.
Never commit it to version control.

### Step 2: Create a Connected App

1. In Salesforce Setup, search for **App Manager** → **New Connected App**.
2. Under **API (Enable OAuth Settings)**:
   - Enable OAuth Settings: **checked**
   - Callback URL: `https://localhost` (unused, required by the form)
   - OAuth Scopes: `api`, `refresh_token`
   - Use digital signatures: **checked** — upload `server.crt`
3. Save and note the **Consumer Key**.

### Step 3: Approve the Connected App

- **Setup → Manage Connected Apps → Policies**
- Set **Permitted Users** to "Admin approved users are pre-authorized"
- Add the running user's Profile or Permission Set

### Step 4: Add the connection in the app

1. Open the web UI and navigate to **Connections**.
2. Create a new connection:
   - **Login URL**: `https://login.salesforce.com` (or `https://test.salesforce.com`)
   - **Client ID**: Consumer Key from Step 2
   - **Username**: the Salesforce user that will run loads
   - **Private Key**: full contents of `server.key` (including PEM headers)
3. Click **Test Connection** to verify.

---

## Preparing CSV Files

Place source CSV files in the `data/input/` directory (Docker) or your configured
input directory (desktop).

Files must:
- Use **UTF-8** encoding (latin-1 and CP-1252 are auto-converted)
- Use **LF** (`\n`) line endings
- Use **Salesforce field API names** as column headers
- Use **`#N/A`** to explicitly null a field

For child objects referencing parents by external ID, use relationship notation:

```csv
FirstName,LastName,Email,Account.ExternalId__c
Jane,Doe,jane@example.com,ACCT-001
```

---

## Creating a Load Plan

1. Navigate to **Load Plans** → **New Plan**.
2. Select the target Salesforce connection.
3. Add **Load Steps** in execution order (parent objects before child objects):
   | Field | Description |
   |-------|-------------|
   | **Object Name** | Salesforce API name (`Account`, `Contact`, etc.) — for query steps this is a free-text label only |
   | **Operation** | `insert`, `update`, `upsert`, `delete`, `query`, or `queryAll` (query-all includes deleted/archived rows) |
   | **External ID Field** | Required for `upsert` (e.g. `ExternalId__c`) |
   | **CSV File Pattern** | DML steps only — glob pattern matching files in the input directory (e.g. `accounts_*.csv`) |
   | **SOQL** | Query steps only — the SOQL statement to execute. Use **Validate SOQL** to check syntax against the org before saving |
   | **Partition Size** | Records per Bulk API job (default 10,000) |
4. Use **Preview** to verify file matching and record counts before running. (Query steps skip preview — use **Validate SOQL** instead.)

### Bulk query steps

Query (`query`) and queryAll (`queryAll`) steps run a SOQL statement via the
Bulk API 2.0 and write the result to a single concatenated CSV artefact on the
plan's configured output connection.

- One file per step, header written once; a header-only file is produced when
  the query returns zero rows.
- `queryAll` includes soft-deleted and archived records.
- **Validate SOQL** uses Salesforce's `explain` endpoint to check syntax and
  surface the query plan before you run the plan.
- To feed query results into a subsequent `delete` (or other DML) step, point
  the downstream step's **CSV File Pattern** at the query's result file.

### Chaining a prior run's output into a DML step

Results written to the local output directory can be used as the input source
for a later plan's DML step:

1. In the Step Editor, set **Input Source** to **Local output files (prior run
   results)**.
2. Click **Browse** to pick a CSV from the output tree, or enter a path
   relative to the output directory in **CSV File Pattern**.

This is useful for two-plan workflows — run a query plan to extract records,
then run a DML plan that points at the query's result file. Paths are resolved
at run time against the local output directory configured on the server.

> Same-run composition (reading output produced earlier in the *same* run) is
> not yet supported, since run-specific output folders don't exist at plan
> edit time. Tracked under SFBL-166.

---

## Running a Load Plan

Click **Run** on the Load Plan page. Monitor job progress in real time on the Load Run view.

DML result files are written to the output directory:

```
{run_id}/{step_id}/{job_id}_success.csv
{run_id}/{step_id}/{job_id}_error.csv
{run_id}/{step_id}/{job_id}_unprocessed.csv
```

Query/queryAll steps write a single concatenated CSV per step (no per-partition
splits). On the Run Detail and Job Detail pages, query jobs show **Rows returned**
and a **Result file** link instead of the DML success/error/unprocessed fields.

---

## Getting notified when a run completes

Subscribe to terminal-state notifications so you don't have to sit on the Runs
page. Available in **hosted profiles only** (`self_hosted`, `aws_hosted`); the
desktop profile has no user identity to attach subscriptions to.

### Channels

- **Email** — plain-text summary built from the `notifications/run_complete`
  template. Rendered subject includes the plan name and terminal status.
- **Webhook** — JSON POST to any `https://` URL. Compatible with Slack
  incoming webhooks. Payload shape:

  ```json
  {
    "text": "Accounts Plan: completed_with_errors",
    "run": {
      "run_id": "…",
      "load_plan_id": "…",
      "plan_name": "Accounts Plan",
      "status": "completed_with_errors",
      "started_at": "2026-04-20T12:00:00Z",
      "finished_at": "2026-04-20T12:04:21Z"
    }
  }
  ```

  `http://` URLs are rejected. The webhook channel retries up to 3 times on
  5xx, 429, or network errors with exponential backoff + jitter; 4xx is
  terminal.

### Triggers

- **Any terminal state** — fires on `completed`, `completed_with_errors`,
  `failed`, or `aborted`.
- **Failures only** — fires on `completed_with_errors`, `failed`, or
  `aborted`.

### Subscribing

Two entry points:

1. **Settings → Notifications** — full CRUD. Choose a channel, destination,
   a specific plan (or "All plans"), and a trigger. Use **Test** to fire a
   synthetic payload through the real dispatcher without waiting for a run.
2. **Plan editor toolbar → "Notify me"** — one-click subscribe for the
   current user's email, scoped to the plan you're editing, with the "any
   terminal" trigger. The button flips to **Notifications on** once a match
   exists, with a menu to Edit or Unsubscribe. **Customize…** opens the full
   form pre-filled with the plan.

### Validating a webhook destination before subscribing

To confirm your endpoint accepts the payload, send a sample request with
`curl` (replace the URL with your endpoint):

```bash
curl -X POST https://hooks.example.com/services/T/B/X \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "Accounts Plan: completed",
    "run": {
      "run_id": "00000000-0000-0000-0000-000000000000",
      "load_plan_id": "00000000-0000-0000-0000-000000000000",
      "plan_name": "Accounts Plan",
      "status": "completed",
      "started_at": "2026-04-20T12:00:00Z",
      "finished_at": "2026-04-20T12:01:00Z"
    }
  }'
```

If the endpoint returns 2xx, the Settings **Test** button will succeed with
the same payload shape.

### Delivery audit trail

Every dispatch writes one row to the `notification_delivery` table capturing
the channel, status, attempt count, last error (if any), and — for email —
the linked `email_delivery` row. Test-send rows carry `is_test=TRUE` and
`run_id=NULL`.

---

## Profile & Password

These features are available in **hosted profiles** (`self_hosted`, `aws_hosted`)
where user authentication is enabled. The desktop profile (`auth_mode=none`) has
no user accounts, so the profile menu and password flows are not shown.

### Update your display name

1. Click your avatar or name in the bottom-left corner of the sidebar.
2. Select **Profile** from the popover.
3. Edit your **Display name** and click **Save**.

Changes are immediate and reflected across the UI.

### Update your email address

Your email address is used for password resets and account notifications.

1. Open your **Profile** page (see above).
2. Enter a new email in the **Email** field and click **Request change**.
3. A verification link is sent to the **new** address. A change-notice email is
   also sent to your **current** address so you are aware of the request.
4. Click the link in the verification email to confirm the change.
5. The link expires after a short window — request a new one if it has lapsed.

### Change your password

1. Open your **Profile** page.
2. Enter your **Current password**, then your **New password** twice.
3. Click **Change password**.
4. A new session token is issued immediately — any other open browser tabs will
   require you to log in again (this is intentional for security).

Password requirements: at least 12 characters, with a mix of upper-case,
lower-case, digits, and special characters.

### Forgot your password

1. On the login page, click **Forgot password?**
2. Enter the email address associated with your account and click **Send reset link**.
3. A reset link is sent to that address. The response is the same whether the
   address is registered or not — this prevents account enumeration.
4. Click the link in the email to set a new password.
5. The link expires after a short window and can only be used once.

### Rate limiting

Password-reset and email-change requests are rate-limited to protect against
abuse. If you receive a "Too many requests" error, wait a short time before
trying again.

---

## Troubleshooting Salesforce Errors

### `invalid_grant` on connection test

- Verify the **Consumer Key** matches the Connected App exactly.
- Confirm the user has been granted the Connected App via Profile or Permission Set.
- Check the server clock — JWT `exp` claims are time-sensitive.
- For sandboxes, ensure **Login URL** is `https://test.salesforce.com`.
