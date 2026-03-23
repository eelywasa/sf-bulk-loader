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
   | **Object Name** | Salesforce API name (`Account`, `Contact`, etc.) |
   | **Operation** | `insert`, `update`, `upsert`, or `delete` |
   | **External ID Field** | Required for `upsert` (e.g. `ExternalId__c`) |
   | **CSV File Pattern** | Glob pattern matching files in the input directory (e.g. `accounts_*.csv`) |
   | **Partition Size** | Records per Bulk API job (default 10,000) |
4. Use **Preview** to verify file matching and record counts before running.

---

## Running a Load Plan

Click **Run** on the Load Plan page. Monitor job progress in real time on the Load Run view.

Result files are written to the output directory:

```
{run_id}/{step_id}/{job_id}_success.csv
{run_id}/{step_id}/{job_id}_error.csv
{run_id}/{step_id}/{job_id}_unprocessed.csv
```

---

## Troubleshooting Salesforce Errors

### `invalid_grant` on connection test

- Verify the **Consumer Key** matches the Connected App exactly.
- Confirm the user has been granted the Connected App via Profile or Permission Set.
- Check the server clock — JWT `exp` claims are time-sensitive.
- For sandboxes, ensure **Login URL** is `https://test.salesforce.com`.
