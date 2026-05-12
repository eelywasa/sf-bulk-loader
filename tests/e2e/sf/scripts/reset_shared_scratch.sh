#!/usr/bin/env bash
#
# Reset a long-lived "shared" Tier 2 scratch org back to a clean state.
#
# Wipes every record across all SObjects in wipe-targets.yml whose
# External_Id__c starts with the `E2E-` prefix.  Each Tier 2 run already
# cleans up its own records via wipe_test_records.py in afterEach, so this
# script is for **forensic** resets — when a failed spec left cruft behind,
# or when you want a known-clean state before a fresh batch of iterations.
#
# Usage:
#   ./reset_shared_scratch.sh <scratch-alias>
#
# Example:
#   ./reset_shared_scratch.sh tier2-shared
#
# Safety:
#   wipe_test_records.py validates the prefix is SOQL-safe (no `_`, `%`,
#   quotes, semicolons, spaces, or non-`[A-Za-z0-9-]` chars).  `E2E-` passes;
#   the constant cannot be overridden — this script is hard-wired to that
#   prefix to prevent accidental broad wipes.
#
# Tier 3 reset (full re-provisioning):
#   This script does NOT delete the scratch org itself.  If the scratch has
#   hit its 30-day max lifetime or has schema divergence too far gone to
#   reset, delete it and provision fresh:
#     sf org delete scratch --target-org <alias> --no-prompt
#     gh workflow run e2e-tier-2.yml -f skip_destroy=true   # fires + keeps the new scratch
#     # then capture its sfdxAuthUrl into TIER2_REUSE_AUTH_URL secret
#
# See docs/deployment/salesforce-jwt-setup.md § "Iterating on Tier 2 with a
# shared scratch" for the full operator runbook.

set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <scratch-alias>" >&2
  echo "       wipes all E2E-prefixed records from the named scratch org" >&2
  exit 2
fi

SCRATCH_ALIAS="$1"
REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
WIPE_SCRIPT="$REPO_ROOT/tests/e2e/sf/scripts/wipe_test_records.py"

if [ ! -f "$WIPE_SCRIPT" ]; then
  echo "ERROR: cannot find wipe_test_records.py at $WIPE_SCRIPT" >&2
  exit 1
fi

# Verify the alias is authenticated and reachable before issuing any wipes.
echo "[reset_shared_scratch] verifying scratch org '${SCRATCH_ALIAS}' is reachable ..." >&2
if ! sf org display --target-org "$SCRATCH_ALIAS" --json >/dev/null 2>&1; then
  echo "ERROR: '${SCRATCH_ALIAS}' is not authenticated in this sf CLI state." >&2
  echo "  Run: sf org login web --alias ${SCRATCH_ALIAS} --instance-url <scratch-instance-url>" >&2
  echo "  (or sf org login sfdx-url from a TIER2_REUSE_AUTH_URL captured earlier)" >&2
  exit 1
fi

echo "[reset_shared_scratch] wiping E2E-prefixed records across all SObjects in wipe-targets.yml ..." >&2
python3 "$WIPE_SCRIPT" \
  --target-org "$SCRATCH_ALIAS" \
  --prefix "E2E-"

echo "[reset_shared_scratch] done." >&2
echo "[reset_shared_scratch] scratch is back to known-clean state for next Tier 2 run." >&2
