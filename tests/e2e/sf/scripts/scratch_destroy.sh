#!/usr/bin/env bash
# scratch_destroy.sh — Tear down the E2E scratch org after Tier 2 tests.
#
# Required env vars:
#   E2E_SCRATCH_ORG   Alias of the scratch org to delete.
#
# Intended to be called in a workflow step with `if: always()` so scratch
# orgs are cleaned up even if upstream steps fail (per spec D3).
#
# Spec refs: D3 (scratch-org always-run teardown).

set -euo pipefail

if [[ -z "${E2E_SCRATCH_ORG:-}" ]]; then
  echo "WARNING: E2E_SCRATCH_ORG is not set — nothing to destroy." >&2
  exit 0
fi

echo "INFO: Deleting scratch org alias='${E2E_SCRATCH_ORG}'"

# --no-prompt suppresses the interactive confirmation prompt in CI.
sf org delete scratch \
  --target-org "${E2E_SCRATCH_ORG}" \
  --no-prompt

echo "INFO: Scratch org '${E2E_SCRATCH_ORG}' deleted successfully."
