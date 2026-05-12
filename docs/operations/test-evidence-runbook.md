# Test evidence runbook

> SFBL-334 / SFBL-350. Operates the GitHub-OAuth-gated test evidence
> dashboard (`reports.bulkloader.forcetide.net`) deployed by
> `BulkLoader-TestEvidence` in us-east-1. See
> [`docs/architecture/aws-topology.md`](../architecture/aws-topology.md#test-evidence-host-sfbl-334)
> for the topology view.

## What this covers

- Initial provisioning of the GitHub OAuth App + seeding the Secrets
  Manager entry the Lambda@Edge reads at cold start.
- Rotating the OAuth client secret and the session signing key.
- Revoking access fast in an incident.
- Granting / revoking individual dashboard access.
- Common failure modes + how to diagnose them.

The runbook is written for **operator AWS-CLI usage**. The shell prompt
must be authenticated against AWS account `628709410721` (the same
account that owns the `BulkLoader-TestEvidence` stack); confirm with
`aws sts get-caller-identity` before any of the commands below.

## Glossary

| Term | Meaning |
| --- | --- |
| **OAuth App** | A registered application on `github.com` that lets users sign in with their GitHub identity. Owns a `clientId` (public) and `clientSecret` (kept private). |
| **Authorization callback URL** | The URL GitHub redirects the browser back to after a successful OAuth sign-in. For this dashboard: `https://reports.bulkloader.forcetide.net/__/auth/callback`. |
| **`sessionSigningKey`** | 32-byte random value the Lambda@Edge uses to HMAC-sign session cookies and OAuth `state` parameters. Rotating it instantly invalidates every live session. |
| **Collaborator** | A GitHub user explicitly added to a repo via Settings → Collaborators. The dashboard admits only collaborators on `eelywasa/sf-bulk-loader`, regardless of role (Read works). |
| **`sfbl/test-evidence/oauth`** | The friendly name of the AWS Secrets Manager secret that holds `clientId`, `clientSecret`, and `sessionSigningKey`. Created by SFBL-341's CDK stack as an empty shell; populated by this runbook. |

## Initial provisioning

Done once per environment. Repeat after a full stack teardown.

### 1. Register the GitHub OAuth App

OAuth Apps register against either a personal account or a GitHub
organization. The dashboard's allowlist is repo-collaborator-based
(not org-membership), so either ownership works — pick whichever
account owns `eelywasa/sf-bulk-loader`.

1. Open https://github.com/settings/developers (personal) or
   `https://github.com/organizations/<org>/settings/applications` (org).
2. Click **New OAuth App**.
3. Fill in:
   - **Application name**: `SFBL Test Evidence Dashboard`
   - **Homepage URL**: `https://reports.bulkloader.forcetide.net`
   - **Application description** (optional): `OAuth gate for the SFBL Allure test-evidence reports (SFBL-334).`
   - **Authorization callback URL**: `https://reports.bulkloader.forcetide.net/__/auth/callback`
     — exact match; trailing slashes matter.
4. Click **Register application**.
5. On the resulting App settings page, copy the **Client ID** (visible
   on the page, public-safe).
6. Click **Generate a new client secret**. Copy the secret immediately
   — GitHub only shows it once.

OAuth App scopes are **not** configured here; the Lambda@Edge requests
`read:user` + `public_repo` at sign-in time on a per-user basis. The
user sees a consent screen the first time they authorise the app.

### 2. Generate the session signing key

Run on a trusted machine. The key never leaves Secrets Manager
afterwards; you do not need to keep a local copy.

```bash
openssl rand -hex 32
```

This emits 64 hex characters (32 bytes). Copy the output for the
next step.

### 3. Seed AWS Secrets Manager

The Secrets Manager entry is created by SFBL-341's CDK stack as an
empty shell named `sfbl/test-evidence/oauth`. Populate it in a single
CLI call so the three values land atomically.

```bash
aws secretsmanager put-secret-value \
  --region us-east-1 \
  --secret-id sfbl/test-evidence/oauth \
  --secret-string '{
    "clientId": "<paste Client ID from step 1.5>",
    "clientSecret": "<paste Client Secret from step 1.6>",
    "sessionSigningKey": "<paste output of openssl rand -hex 32 from step 2>"
  }'
```

The Lambda@Edge fetches this secret by name on cold start and caches
the result for the lifetime of the execution environment. After
seeding, a **new** request to the dashboard (one that triggers a cold
start in the nearest CloudFront edge region) will pick up the values
within seconds. Already-running execution environments may take up
to ~10 minutes to cycle.

### 4. Verify the seeding

```bash
# Confirm all three fields are present (values not echoed).
aws secretsmanager get-secret-value \
  --region us-east-1 \
  --secret-id sfbl/test-evidence/oauth \
  --query SecretString --output text \
  | jq 'keys'
# Expected output: [ "clientId", "clientSecret", "sessionSigningKey" ]
```

### 5. End-to-end smoke test

After Route53 alias creation (operator step, captured in SFBL-341's
deploy notes) and CloudFront propagation (~15 min), run the three
cases from SFBL-341's AC:

```bash
# 1. Unauthenticated → 302 to GitHub OAuth
curl -sI https://reports.bulkloader.forcetide.net/ | head -3
# Expected: HTTP/2 302 with Location: https://github.com/login/oauth/authorize?...

# 2. Authenticated non-collaborator → 403
# (Open the URL in a browser as a GitHub user who is NOT on
#  https://github.com/eelywasa/sf-bulk-loader/settings/access)
# Expected: 403 page with the "Access denied" message naming the user.

# 3. Authenticated read-only collaborator → admitted
# (Add a test user as a Read collaborator first; have them open the URL.)
# Expected: pass through to the report (or to a 404/empty bucket if
#           nothing has been published yet).
```

## Rotation

### Client secret rotation

Routine; do every 90 days or after any suspected leak.

1. On GitHub, open the OAuth App settings page. Click
   **Generate a new client secret**. Copy the new secret.
2. **Do not delete the old secret yet** — GitHub keeps both valid
   during the transition window. This lets in-flight OAuth flows
   complete with the old secret while new ones use the rotated value.
3. Update Secrets Manager with the new value:
   ```bash
   # Read current secret to keep the other fields unchanged.
   aws secretsmanager get-secret-value \
     --region us-east-1 \
     --secret-id sfbl/test-evidence/oauth \
     --query SecretString --output text > /tmp/sfbl-secret.json

   # Edit /tmp/sfbl-secret.json — replace clientSecret with the new value.
   # (Keep clientId and sessionSigningKey as-is.)

   aws secretsmanager put-secret-value \
     --region us-east-1 \
     --secret-id sfbl/test-evidence/oauth \
     --secret-string file:///tmp/sfbl-secret.json

   rm /tmp/sfbl-secret.json   # clear local copy
   ```
4. Wait ~10 minutes for Lambda@Edge cold-start cache to cycle, then
   test a fresh sign-in to confirm the new secret works.
5. Once confirmed, delete the **old** client secret on the GitHub
   OAuth App settings page so only the new one is valid.

### Session signing key rotation

Routine; do every 180 days OR immediately to invalidate all live
sessions (e.g. after a collaborator is removed and you want their
remaining cookie TTL voided).

```bash
NEW_KEY=$(openssl rand -hex 32)
aws secretsmanager get-secret-value \
  --region us-east-1 \
  --secret-id sfbl/test-evidence/oauth \
  --query SecretString --output text \
  | jq --arg key "$NEW_KEY" '.sessionSigningKey = $key' \
  > /tmp/sfbl-secret.json

aws secretsmanager put-secret-value \
  --region us-east-1 \
  --secret-id sfbl/test-evidence/oauth \
  --secret-string file:///tmp/sfbl-secret.json

rm /tmp/sfbl-secret.json
unset NEW_KEY
```

The next cold start of the Lambda@Edge picks up the new key. Every
session cookie signed by the old key fails the HMAC verification and
the user is bounced to GitHub OAuth to re-authenticate. Warm execution
environments retain the old key in module-scope cache until they
cycle (~10 min worst case). For a guaranteed-immediate flush, publish
a new Lambda version (re-deploys the stack — usually overkill).

## Incident revocation

### Suspected credential leak (clientSecret only)

Follow **Client secret rotation** above but **do not** wait for the
transition window — delete the leaked secret on the GitHub OAuth App
settings page immediately after the new one is in Secrets Manager.
Active sign-in attempts using the leaked secret will fail; live
sessions (signed with the unchanged `sessionSigningKey`) keep working.

### Suspected session token leak / suspected unauthorised access

Rotate **`sessionSigningKey`** per the procedure above. Every live
session is invalidated on the next cold start (~seconds at the nearest
edge, ≤10 min worst case across all edges). Users get bounced to
GitHub OAuth and re-authenticate cleanly.

### Total lockdown (e.g. compromised OAuth App)

1. On GitHub, open the OAuth App settings page and click
   **Delete OAuth App**. Confirm. This breaks every future sign-in.
2. The dashboard URL still serves the configured 500 error page (the
   Lambda@Edge can't load a working OAuth client). To make the URL
   return 404, point the Route53 alias somewhere else or scale the
   CloudFront distribution down via `aws cloudfront update-distribution`
   (set `Enabled: false`).
3. To recover: re-run the **Initial provisioning** procedure above.
   The Secrets Manager entry can be reseeded with the new App's
   credentials — the stack itself doesn't need redeploy.

## Granting / revoking dashboard access

The single source of truth is GitHub's Collaborators list on
`eelywasa/sf-bulk-loader`. There is no separate Secrets-Manager
allowlist override — adding someone via the GitHub UI is the only path.

### Grant

1. Open https://github.com/eelywasa/sf-bulk-loader/settings/access.
2. Click **Add people** (or **Invite a collaborator**).
3. Enter the user's GitHub username or email.
4. Pick any role from the **Permission** dropdown. **Read is fine** —
   the Lambda@Edge admits collaborators of any role, including the
   most restrictive one. Don't grant Write unless they actually need
   to push code to the repo.
5. The user receives a GitHub invite; once they accept, they can open
   `https://reports.bulkloader.forcetide.net` and complete OAuth.

### Revoke

1. Open https://github.com/eelywasa/sf-bulk-loader/settings/access.
2. Click the **X** next to the user; confirm.
3. The user's **existing session cookie** keeps working until it
   expires (8h TTL). They can still browse the dashboard during that
   window. For incident-grade immediate revocation, also rotate
   `sessionSigningKey` per the rotation procedure above.

### Why not org membership or a separate allowlist?

- The repo is **public**, so `pull` access is meaningless as an
  authorization signal (every GitHub user has it).
- `eelywasa` is a personal account, not an organization, so the
  org-membership-based pattern doesn't apply.
- A separate Secrets-Manager allowlist would duplicate state and add
  rotation drift. Keeping the GitHub Collaborators list as the only
  source means there's one place to look and one place to update.

## Common failure modes

### `Access denied` (403) despite the user being a collaborator

Most likely causes:

- **The user hasn't accepted the GitHub collaborator invite yet.**
  Check the Collaborators page on `eelywasa/sf-bulk-loader` — pending
  invites show as such. Until accepted, the user is not yet considered
  a collaborator by the GitHub API.
- **Stale session.** The 403 page reflects the username from the most
  recent OAuth sign-in. If a user was demoted and then re-promoted,
  ask them to sign out (clear the `evidence_session` cookie) and
  retry.

### `Configuration error` (500) on every request

The Lambda@Edge couldn't load the Secrets Manager entry. Causes:

- **The secret isn't seeded yet.** Run the verification command in
  step 4 above; if `jq 'keys'` returns the wrong field set, re-seed
  with step 3.
- **Missing field.** The Lambda explicitly checks for `clientId`,
  `clientSecret`, and `sessionSigningKey`. A typo in any of them
  triggers the 500.
- **IAM permission issue.** The Lambda@Edge execution role
  (`LambdaEdgeRole` in the stack) must have
  `secretsmanager:GetSecretValue` on `sfbl/test-evidence/oauth`. CDK
  wires this automatically; check the role's inline policy if you
  suspect it's been edited manually.

For all three: check the Lambda@Edge CloudWatch logs in the edge
region nearest to where the test originated. Logs are emitted to
`/aws/lambda/us-east-1.<function-name>` in the closest of the
CloudFront edge regions (`us-east-1`, `eu-west-1`, `ap-northeast-1`,
etc.). The Lambda logs the error message before returning the 500.

### OAuth callback URL mismatch

If the GitHub OAuth flow returns the user to a URL that the App
doesn't recognise, GitHub shows its own error page (not ours). The
**Authorization callback URL** on the OAuth App settings page must
be exactly:

```
https://reports.bulkloader.forcetide.net/__/auth/callback
```

Trailing slashes, scheme, and case all matter. The Lambda hardcodes
this URL via the synth-time substituted `CALLBACK_URL` constant; if
the dashboard's custom domain ever changes, both the OAuth App
settings AND `infrastructure/cdk.context.json` need updating in
lockstep.

### Sign-in works but the user immediately gets bounced back to GitHub

The session cookie is being rejected. Causes:

- **`sessionSigningKey` was rotated and the cookie was signed with the
  old key.** The user re-signs in cleanly on the second attempt —
  this is the expected post-rotation behaviour.
- **The cookie hit the 8h TTL.** Same outcome; the user re-signs in.
- **Clock skew / Lambda environment with wrong time.** Lambda@Edge
  uses the AWS edge region's clock which is NTP-synced; a clock-skew
  bug would affect every user. If only one user is affected, suspect
  cookie issues on their browser.

## See also

- [`docs/architecture/aws-topology.md`](../architecture/aws-topology.md#test-evidence-host-sfbl-334) —
  topology + request-path diagrams for the test-evidence stack.
- [SFBL-341](https://matthew-jenkin.atlassian.net/browse/SFBL-341) —
  the CDK stack this runbook operates.
- [SFBL-334](https://matthew-jenkin.atlassian.net/browse/SFBL-334) —
  the parent epic for the cross-layer Allure test reporting work.
