#!/usr/bin/env bash
# disable_duplicate_rules.sh — Deactivate the scratch org's active DuplicateRule
# records so Bulk API 2.0 inserts don't blanket-fail with
# `DUPLICATES_DETECTED:Duplicate Alert`.
#
# WHY this exists:
#   Spring '26 scratch orgs ship with three active "Standard *_Duplicate_Rule"
#   records (Account, Contact, Lead).  These rules trigger on fuzzy Name
#   matches and reject inserts via the Bulk API once a few similar records
#   are already in the org.  The default action is `Block`, not `Allow`,
#   so every row in a 50-row Account insert can fail with
#     DUPLICATES_DETECTED:Duplicate Alert:--
#   even when the External_Id__c is unique (the rule doesn't consult it).
#
#   Bulk API 2.0 has no header to bypass duplicate detection.
#
# WHY the Metadata API (not the Tooling API or Apex):
#   * Tooling API:  `sf data query --use-tooling-api ... DuplicateRule`
#                   returns INVALID_TYPE 'DuplicateRule' is not supported.
#   * Apex update:  DuplicateRule.IsActive is read-only at the Apex layer
#                   ("Field is not writeable").
#   * Metadata:     <isActive> can be flipped to false in the
#                   .duplicateRule-meta.xml file and redeployed.  This is
#                   the only path that works end-to-end via `sf`.
#
# IDEMPOTENT: retrieves the current rule files, flips active→inactive in
# the XML, and redeploys.  Re-running once the rules are already inactive
# is a no-op deploy (no <isActive>true</isActive> to flip).
#
# Usage:   ./disable_duplicate_rules.sh
# Env:     E2E_SCRATCH_ORG — target scratch org alias.
# Exits 0 on success, 1 on any failure.

set -euo pipefail

: "${E2E_SCRATCH_ORG:?E2E_SCRATCH_ORG must be set (scratch org alias)}"

echo "[disable_duplicate_rules] target org: ${E2E_SCRATCH_ORG}" >&2

# ── Stand up a throw-away SFDX project ────────────────────────────────────────
PROJ_DIR="$(mktemp -d /tmp/dup-rules-XXXXXX)"
trap 'rm -rf "$PROJ_DIR"' EXIT

cat > "${PROJ_DIR}/sfdx-project.json" <<'JSON'
{
  "packageDirectories": [{ "path": "force-app", "default": true }],
  "name": "dup-rules-disable",
  "namespace": "",
  "sfdcLoginUrl": "https://login.salesforce.com",
  "sourceApiVersion": "62.0"
}
JSON
mkdir -p "${PROJ_DIR}/force-app/main/default"

# ── Retrieve every active DuplicateRule on the org ────────────────────────────
# `--metadata DuplicateRule` pulls all of them at once.  Salesforce returns
# files at force-app/main/default/duplicateRules/<Object>.<RuleName>.duplicateRule-meta.xml
echo "[disable_duplicate_rules] retrieving DuplicateRule metadata ..." >&2
cd "$PROJ_DIR"
set +e
RETRIEVE_OUT="$(sf project retrieve start \
  --metadata DuplicateRule \
  --target-org "${E2E_SCRATCH_ORG}" \
  --json 2>&1)"
SF_EXIT=$?
set -e
cd - >/dev/null
if [[ $SF_EXIT -ne 0 ]]; then
  echo "[disable_duplicate_rules] retrieve failed with exit ${SF_EXIT}" >&2
  echo "$RETRIEVE_OUT" >&2
  exit 1
fi

DUP_DIR="${PROJ_DIR}/force-app/main/default/duplicateRules"
if [[ ! -d "$DUP_DIR" ]]; then
  echo "[disable_duplicate_rules] no DuplicateRule metadata returned — nothing to do." >&2
  exit 0
fi

RULE_FILES=("$DUP_DIR"/*.duplicateRule-meta.xml)
if [[ ! -e "${RULE_FILES[0]}" ]]; then
  echo "[disable_duplicate_rules] no rule files found in $DUP_DIR — nothing to do." >&2
  exit 0
fi

# ── Flip <isActive>true</isActive> → <isActive>false</isActive> in each file ──
CHANGED_COUNT=0
for f in "${RULE_FILES[@]}"; do
  name="$(basename "$f" .duplicateRule-meta.xml)"
  if grep -q "<isActive>true</isActive>" "$f"; then
    sed -i 's|<isActive>true</isActive>|<isActive>false</isActive>|g' "$f"
    echo "[disable_duplicate_rules] flipping ${name} to inactive" >&2
    CHANGED_COUNT=$((CHANGED_COUNT + 1))
  else
    echo "[disable_duplicate_rules] ${name} already inactive — skipping" >&2
  fi
done

if [[ $CHANGED_COUNT -eq 0 ]]; then
  echo "[disable_duplicate_rules] all rules already inactive — done." >&2
  exit 0
fi

# ── Redeploy the modified rule files ──────────────────────────────────────────
echo "[disable_duplicate_rules] deploying ${CHANGED_COUNT} deactivated rule(s) ..." >&2
cd "$PROJ_DIR"
set +e
DEPLOY_OUT="$(sf project deploy start \
  --metadata DuplicateRule \
  --target-org "${E2E_SCRATCH_ORG}" \
  --wait 10 \
  --ignore-conflicts \
  --json 2>&1)"
SF_EXIT=$?
set -e
cd - >/dev/null
if [[ $SF_EXIT -ne 0 ]]; then
  echo "[disable_duplicate_rules] deploy failed with exit ${SF_EXIT}" >&2
  echo "$DEPLOY_OUT" >&2
  exit 1
fi

echo "[disable_duplicate_rules] done — ${CHANGED_COUNT} rule(s) deactivated." >&2
