#!/usr/bin/env bash
# scratch_destroy.sh — Tear down the E2E scratch org after Tier 2 tests.
#
# TODO (SFBL-321/325/327): Implement scratch-org teardown.
#   - Run in an always() step so failures don't leak orgs
#   - Delete the org identified by E2E_SCRATCH_ORG
#
# This stub exits with a clear error so any premature invocation is visible.

set -euo pipefail

echo "ERROR: scratch_destroy.sh is not yet implemented." >&2
echo "  This script will be populated by SFBL-321/325/327 (Wave 3)." >&2
exit 1
