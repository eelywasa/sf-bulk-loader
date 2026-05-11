#!/usr/bin/env bash
# disable_duplicate_rules.sh — Deactivate the scratch org's active DuplicateRule
# records so Bulk API 2.0 inserts don't blanket-fail with
# `DUPLICATES_DETECTED:Duplicate Alert`.
#
# WHY this exists:
#   Spring '26 scratch orgs ship with three active "Standard *_Duplicate_Rule"
#   records (Account, Contact, Lead).  These rules trigger on fuzzy Name
#   matches and reject inserts via the Bulk API once a few similar names
#   are already in the org.  The default action is `Block`, not `Allow`,
#   so every row in a 50-row Account insert fails with
#     DUPLICATES_DETECTED:Duplicate Alert:--
#   even when the External_Id__c is unique (the rule doesn't consult it).
#
#   Bulk API 2.0 has no header to bypass duplicate detection — the only
#   reliable path is to flip IsActive=false on the rules.
#
# WHY anonymous Apex (not the Tooling API):
#   `sf data query --use-tooling-api ... DuplicateRule` returns
#     INVALID_TYPE: sObject type 'DuplicateRule' is not supported.
#   even though DuplicateRule is queryable in Apex.  Easiest path is
#   to run a tiny anonymous-Apex block that does the query + DML in
#   one round-trip.
#
# IDEMPOTENT: the Apex script touches only IsActive=true rules. Safe to
#   run on every reuse-mode invocation.
#
# Usage:   ./disable_duplicate_rules.sh
# Env:     E2E_SCRATCH_ORG — target scratch org alias.
# Exits 0 on success, 1 on any failure.

set -euo pipefail

: "${E2E_SCRATCH_ORG:?E2E_SCRATCH_ORG must be set (scratch org alias)}"

echo "[disable_duplicate_rules] target org: ${E2E_SCRATCH_ORG}" >&2

APEX_FILE="$(mktemp /tmp/disable-dup-rules-XXXXXX.apex)"
trap 'rm -f "$APEX_FILE"' EXIT

cat > "$APEX_FILE" <<'APEX'
List<DuplicateRule> rules = [
  SELECT Id, DeveloperName, IsActive
  FROM DuplicateRule
  WHERE IsActive = true
];
if (rules.isEmpty()) {
  System.debug('[disable_duplicate_rules] no active rules — nothing to do.');
} else {
  for (DuplicateRule r : rules) {
    System.debug('[disable_duplicate_rules] deactivating ' + r.DeveloperName + ' (' + r.Id + ')');
    r.IsActive = false;
  }
  update rules;
  System.debug('[disable_duplicate_rules] deactivated ' + rules.size() + ' rule(s).');
}
APEX

set +e
APEX_OUT="$(sf apex run \
  --file "$APEX_FILE" \
  --target-org "${E2E_SCRATCH_ORG}" \
  --json 2>&1)"
SF_EXIT=$?
set -e

if [[ $SF_EXIT -ne 0 ]]; then
  echo "[disable_duplicate_rules] sf apex run failed with exit ${SF_EXIT}" >&2
  echo "[disable_duplicate_rules] sf output below:" >&2
  echo "$APEX_OUT" >&2
  exit 1
fi

# Verify success: sf apex run returns JSON with .result.success=true (or
# .result.compiled=true && .result.success=true) on a clean run.
echo "$APEX_OUT" | python3 - <<'PYEOF'
import json
import sys

raw = sys.stdin.read()
data = json.loads(raw)
result = data.get("result", {})
compiled = result.get("compiled")
success = result.get("success")
if not compiled:
    print(f"[disable_duplicate_rules] apex did not compile: {result.get('compileProblem')}", file=sys.stderr)
    sys.exit(1)
if not success:
    print(f"[disable_duplicate_rules] apex did not succeed: {result.get('exceptionMessage')}", file=sys.stderr)
    print(f"[disable_duplicate_rules] stack: {result.get('exceptionStackTrace')}", file=sys.stderr)
    sys.exit(1)
# Surface the debug log lines our script emitted (System.debug output).
for line in (result.get("logs") or "").splitlines():
    if "[disable_duplicate_rules]" in line:
        print(line, file=sys.stderr)
PYEOF

echo "[disable_duplicate_rules] done." >&2
