#!/usr/bin/env bash
# Assign the SfblE2EPermSet permission set to the scratch org's admin user
# AND create the SetupEntityAccess linking record that activates
# AdminApprovedPreAuthorized on the ECA.
#
# WHY both steps are required:
#   Assigning the permission set alone is insufficient for JWT bearer auth.
#   Salesforce requires a SetupEntityAccess record that links the PermissionSet
#   to the ExternalClientApplication (ECA).  Without it the org's
#   AdminApprovedPreAuthorized policy does not activate and every JWT grant
#   returns "user hasn't approved this consumer" or a similar 400 error.
#   (Confirmed by SFBL-332 spike: step 4 of setup_scratch_org.sh.)
#
# WHY SetupEntityType is not specified in the values:
#   The spike confirmed that omitting SetupEntityType causes Salesforce to infer
#   it from the SObject type of SetupEntityId.  The spike used the same
#   invocation successfully, so we match it exactly.
#
# Usage:
#   ./setup_permset_and_access.sh
#
# Environment variables (all required):
#   E2E_SCRATCH_ORG  — scratch org alias (set by scratch_create.sh)
#
# Exit codes:
#   0 — success
#   1 — any failure (permset assignment failed, SOQL lookup failed, record creation failed)

set -euo pipefail

# ── Validate required env vars ───────────────────────────────────────────────
: "${E2E_SCRATCH_ORG:?E2E_SCRATCH_ORG must be set (scratch org alias)}"

echo "[setup_permset_and_access] target org: ${E2E_SCRATCH_ORG}" >&2

# ── Step 1: Assign the permission set to the scratch admin user ───────────────
echo "[setup_permset_and_access] assigning SfblE2EPermSet ..." >&2
sf org assign permset \
  --name SfblE2EPermSet \
  --target-org "${E2E_SCRATCH_ORG}"

echo "[setup_permset_and_access] permission set assigned." >&2

# ── Step 2: Look up IDs needed for SetupEntityAccess ─────────────────────────
echo "[setup_permset_and_access] resolving ECA and permission-set IDs ..." >&2

ECA_ID="$(sf data query \
  --query "SELECT Id FROM ExternalClientApplication WHERE DeveloperName='SfblE2E'" \
  --target-org "${E2E_SCRATCH_ORG}" \
  --json | python3 -c "
import sys, json
result = json.load(sys.stdin)
records = result.get('result', {}).get('records', [])
if not records:
    print('ERROR: ExternalClientApplication SfblE2E not found.', file=sys.stderr)
    print('  Ensure the ECA metadata was deployed before this step.', file=sys.stderr)
    raise SystemExit(1)
print(records[0]['Id'])
")"

PERMSET_ID="$(sf data query \
  --query "SELECT Id FROM PermissionSet WHERE Name='SfblE2EPermSet'" \
  --target-org "${E2E_SCRATCH_ORG}" \
  --json | python3 -c "
import sys, json
result = json.load(sys.stdin)
records = result.get('result', {}).get('records', [])
if not records:
    print('ERROR: PermissionSet SfblE2EPermSet not found.', file=sys.stderr)
    print('  Ensure the SFDX project was deployed before this step.', file=sys.stderr)
    raise SystemExit(1)
print(records[0]['Id'])
")"

echo "[setup_permset_and_access] ECA Id     : ${ECA_ID}" >&2
echo "[setup_permset_and_access] PermSet Id : ${PERMSET_ID}" >&2

# ── Step 3: Create the SetupEntityAccess linking record ───────────────────────
# WHY this record: AdminApprovedPreAuthorized requires Salesforce to see that
# the ECA (SetupEntityId) is explicitly linked to the permission set (ParentId).
# Without this record the JWT bearer grant returns an auth error even after the
# permission set is assigned to the user.
echo "[setup_permset_and_access] creating SetupEntityAccess record ..." >&2
sf data create record \
  --sobject SetupEntityAccess \
  --values "SetupEntityId=${ECA_ID} ParentId=${PERMSET_ID}" \
  --target-org "${E2E_SCRATCH_ORG}"

echo "[setup_permset_and_access] SetupEntityAccess record created." >&2
echo "[setup_permset_and_access] done — JWT bearer auth should now be active." >&2
