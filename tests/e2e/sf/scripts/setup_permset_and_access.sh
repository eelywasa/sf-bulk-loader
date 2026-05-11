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
#
# SfblE2EPermSet carries two responsibilities now (PR #89):
#   - ECA OAuth gate: linked to the SfblE2E ECA via SetupEntityAccess below.
#   - Custom-field FLS: grants the running user the read/edit access needed for
#     the Tier 2 specs' sf data query calls.  Salesforce does NOT auto-grant
#     FLS to metadata-API-deployed custom fields, even for System Administrator;
#     PR #89 run 25693622361 surfaced "No such column 'External_Id__c' on
#     entity 'Account'" during a sf data query after a successful deploy.
echo "[setup_permset_and_access] assigning SfblE2EPermSet ..." >&2
# Tolerate "Permission set already assigned" so reuse-mode runs (where the
# permset was assigned by a prior workflow invocation) succeed without
# masking real failures. Any other failure still aborts the script.
PERMSET_ASSIGN_OUT="$(sf org assign permset \
  --name SfblE2EPermSet \
  --target-org "${E2E_SCRATCH_ORG}" 2>&1)" || {
  if echo "$PERMSET_ASSIGN_OUT" | grep -qE "Permission set.*already assigned|duplicate value found"; then
    echo "[setup_permset_and_access] permission set already assigned — continuing." >&2
  else
    echo "$PERMSET_ASSIGN_OUT" >&2
    exit 1
  fi
}

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
#
# IDEMPOTENCE: in reuse-mode runs, an SEA already links the same permset to
# *some* ECA Id, but `sf project deploy --ignore-conflicts` may have rotated
# the ECA's internal Id, leaving the previous link stale.  Look up first and
# only create a new SEA if one for this exact (ECA, permset) pair is missing.
EXISTING_SEA_ID="$(sf data query \
  --query "SELECT Id FROM SetupEntityAccess WHERE SetupEntityId='${ECA_ID}' AND ParentId='${PERMSET_ID}' LIMIT 1" \
  --target-org "${E2E_SCRATCH_ORG}" \
  --json | python3 -c "
import sys, json
records = json.load(sys.stdin).get('result', {}).get('records', [])
print(records[0]['Id'] if records else '')
")"

if [[ -n "$EXISTING_SEA_ID" ]]; then
  echo "[setup_permset_and_access] SetupEntityAccess already exists (Id=${EXISTING_SEA_ID}) — skipping create." >&2
else
  echo "[setup_permset_and_access] creating SetupEntityAccess record ..." >&2
  sf data create record \
    --sobject SetupEntityAccess \
    --values "SetupEntityId=${ECA_ID} ParentId=${PERMSET_ID}" \
    --target-org "${E2E_SCRATCH_ORG}"
  echo "[setup_permset_and_access] SetupEntityAccess record created." >&2
fi
echo "[setup_permset_and_access] done — JWT bearer auth should now be active." >&2
