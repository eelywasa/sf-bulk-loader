#!/usr/bin/env bash
# scratch_create.sh — Spin up a Salesforce scratch org for E2E Tier 2 tests.
#
# TODO (SFBL-321/325/327): Implement scratch-org lifecycle.
#   - Authenticate to Dev Hub via JWT (D6)
#   - Create scratch org from config/project-scratch-def.json
#   - Deploy SFDX project (sf/ + app/ metadata)
#   - Export E2E_SCRATCH_ORG for downstream steps
#
# This stub exits with a clear error so any premature invocation is visible.

set -euo pipefail

echo "ERROR: scratch_create.sh is not yet implemented." >&2
echo "  This script will be populated by SFBL-321/325/327 (Wave 3)." >&2
exit 1
