#!/usr/bin/env bash
#
# One-time setup for the E2E Tier 2 CI Dev Hub ECA.
#
# Deploys tests/e2e/sf/sfdx-devhub/force-app to the chosen Dev Hub, assigns the
# permission set to the running user, creates the SetupEntityAccess link
# required to activate AdminApprovedPreAuthorized, then retrieves the freshly
# generated consumer key for pasting into the SFDX_DEVHUB_CONSUMER_KEY GH
# secret.
#
# Usage:
#   bash tests/e2e/sf/scripts/setup_devhub_eca.sh <devhub-alias-or-username>
#
# Prerequisites:
#   - sf CLI authenticated to the Dev Hub (interactive `sf org login web` is
#     fine; the JWT-via-ECA path is what this script SETS UP, not what it
#     consumes).
#   - You are running as the admin user that will be impersonated by CI's
#     JWT bearer grants (typically the Dev Hub admin).
#
# After this script succeeds, paste its final output into:
#   gh secret set SFDX_DEVHUB_CONSUMER_KEY --body '<paste here>'
#
# Then re-fire the Tier 2 workflow.

set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <devhub-alias-or-username>" >&2
  exit 2
fi

DEVHUB="$1"
ECA_NAME="SfblE2ECiDevHub"
PERMSET_NAME="SfblE2ECiDevHubPermSet"
REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
PROJECT_DIR="$REPO_ROOT/tests/e2e/sf/sfdx-devhub"

echo "[setup_devhub_eca] target Dev Hub: $DEVHUB"
echo "[setup_devhub_eca] ECA name      : $ECA_NAME"

# ── 1. Deploy ────────────────────────────────────────────────────────
echo "[setup_devhub_eca] deploying SFDX metadata ..."
(
  cd "$PROJECT_DIR"
  sf project deploy start \
    --target-org "$DEVHUB" \
    --wait 10
)

# ── 2. Assign permission set (idempotent) ────────────────────────────
echo "[setup_devhub_eca] checking permission set assignment ..."
ME="$(sf org display --target-org "$DEVHUB" --json | jq -r '.result.username')"
EXISTING_ASSIGN="$(sf data query \
  --target-org "$DEVHUB" \
  --json \
  -q "SELECT Id FROM PermissionSetAssignment WHERE PermissionSet.Name = '$PERMSET_NAME' AND Assignee.Username = '$ME' LIMIT 1" \
  | jq -r '.result.records[0].Id // empty')"
if [ -n "$EXISTING_ASSIGN" ]; then
  echo "[setup_devhub_eca] PermSet already assigned to $ME ($EXISTING_ASSIGN) — skipping."
else
  echo "[setup_devhub_eca] assigning $PERMSET_NAME to $ME ..."
  sf org assign permset \
    --target-org "$DEVHUB" \
    --name "$PERMSET_NAME"
fi

# ── 3. Create SetupEntityAccess link ─────────────────────────────────
echo "[setup_devhub_eca] resolving ECA and permission-set IDs ..."
ECA_ID="$(sf data query \
  --target-org "$DEVHUB" \
  --json \
  -q "SELECT Id FROM ExternalClientApplication WHERE DeveloperName = '$ECA_NAME' LIMIT 1" \
  | jq -r '.result.records[0].Id')"
PERMSET_ID="$(sf data query \
  --target-org "$DEVHUB" \
  --json \
  -q "SELECT Id FROM PermissionSet WHERE Name = '$PERMSET_NAME' LIMIT 1" \
  | jq -r '.result.records[0].Id')"

if [ -z "$ECA_ID" ] || [ "$ECA_ID" = "null" ]; then
  echo "[setup_devhub_eca] ERROR: could not find ECA '$ECA_NAME' after deploy." >&2
  exit 1
fi
if [ -z "$PERMSET_ID" ] || [ "$PERMSET_ID" = "null" ]; then
  echo "[setup_devhub_eca] ERROR: could not find PermSet '$PERMSET_NAME' after deploy." >&2
  exit 1
fi

echo "[setup_devhub_eca] ECA Id     : $ECA_ID"
echo "[setup_devhub_eca] PermSet Id : $PERMSET_ID"

echo "[setup_devhub_eca] creating SetupEntityAccess record ..."
# Idempotency: check if the linking record already exists; if yes, skip.
EXISTING="$(sf data query \
  --target-org "$DEVHUB" \
  --json \
  -q "SELECT Id FROM SetupEntityAccess WHERE ParentId = '$PERMSET_ID' AND SetupEntityId = '$ECA_ID' LIMIT 1" \
  | jq -r '.result.records[0].Id // empty')"
if [ -n "$EXISTING" ]; then
  echo "[setup_devhub_eca] SetupEntityAccess already exists ($EXISTING) — skipping create."
else
  # SetupEntityType is derived by Salesforce from SetupEntityId and is
  # read-only — do not include it in the create payload (matches
  # setup_permset_and_access.sh for the bulk-loader scratch ECA).
  sf data create record \
    --target-org "$DEVHUB" \
    --sobject SetupEntityAccess \
    --values "ParentId=$PERMSET_ID SetupEntityId=$ECA_ID"
fi

# ── 4. Discover consumer key ─────────────────────────────────────────
echo "[setup_devhub_eca] retrieving consumer key ..."
TMP_DIR="$(mktemp -d)"
trap "rm -rf $TMP_DIR" EXIT
cp -R "$PROJECT_DIR/." "$TMP_DIR/"
(
  cd "$TMP_DIR"
  sf project retrieve start \
    --target-org "$DEVHUB" \
    --metadata "ExtlClntAppGlobalOauthSettings:$ECA_NAME" \
    >/dev/null
)
KEY_FILE="$TMP_DIR/force-app/main/default/extlClntAppGlobalOauthSets/$ECA_NAME.ecaGlblOauth-meta.xml"
CONSUMER_KEY="$(grep -oE '<consumerKey>[^<]+</consumerKey>' "$KEY_FILE" | sed -E 's,</?consumerKey>,,g' | head -1)"

if [ -z "$CONSUMER_KEY" ]; then
  echo "[setup_devhub_eca] ERROR: failed to extract consumer key from retrieved metadata." >&2
  echo "[setup_devhub_eca] retrieved file:" >&2
  cat "$KEY_FILE" >&2
  exit 1
fi

echo
echo "════════════════════════════════════════════════════════════════"
echo "[setup_devhub_eca] DONE."
echo
echo "  Dev Hub               : $DEVHUB"
echo "  ECA DeveloperName     : $ECA_NAME"
echo "  ECA Id                : $ECA_ID"
echo "  Consumer Key          : $CONSUMER_KEY"
echo
echo "Next: update the GitHub Actions secret with the new consumer key:"
echo
echo "  gh secret set SFDX_DEVHUB_CONSUMER_KEY --body '$CONSUMER_KEY'"
echo
echo "  (Verify SFDX_DEVHUB_INSTANCE_URL also points at this Dev Hub's"
echo "   My Domain URL — find via:"
echo "     sf org display --target-org $DEVHUB --json | jq -r '.result.instanceUrl')"
echo "════════════════════════════════════════════════════════════════"
