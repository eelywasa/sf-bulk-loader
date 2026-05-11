#!/usr/bin/env bash
# Discover the SfblE2E ECA consumer key from a scratch org.
#
# WHY this mechanism:
#   The consumer key lives in ExtlClntAppGlobalOauthSettings metadata (the
#   "global OAuth settings" for an External Client App), not in any SOQL-
#   queryable field.  The REST endpoint /connect/external-client-apps/<name>
#   does NOT exist, and ExternalClientApplication records exposed via SOQL do
#   NOT surface the consumer key.  The only reliable path is:
#     sf project retrieve start --metadata "ExtlClntAppGlobalOauthSettings:SfblE2E"
#   which pulls the XML that contains <consumerKey>.
#
# The retrieved file is sanitised before exit — <consumerKey> and
# <consumerSecret> are stripped so they can never land in source control.
# The public certificate (safe to commit) is preserved.
#
# Usage:
#   ./discover_eca_consumer_key.sh
#
# Environment variables (all required):
#   E2E_SCRATCH_ORG  — scratch org alias (set by scratch_create.sh)
#
# Outputs:
#   stdout          — the consumer key string (and only that, on the last line)
#   E2E_BULK_LOADER_CONSUMER_KEY — set in the caller's environment via
#                                  >> $GITHUB_ENV in the workflow step.
#
# Exit codes:
#   0 — success; consumer key printed to stdout
#   1 — any failure (metadata retrieve failed, XML missing, consumerKey absent)

set -euo pipefail

# ── Resolve paths ────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
SFDX_DIR="$REPO_ROOT/tests/e2e/sf/sfdx"

# ── Validate required env vars ───────────────────────────────────────────────
: "${E2E_SCRATCH_ORG:?E2E_SCRATCH_ORG must be set (scratch org alias)}"

echo "[discover_eca_consumer_key] target org: ${E2E_SCRATCH_ORG}" >&2

# ── Retrieve the ECA global OAuth settings metadata ──────────────────────────
# This is the ONLY place the consumerKey is accessible programmatically.
# The retrieve writes the XML to:
#   force-app/main/default/extlClntAppGlobalOauthSets/SfblE2E.ecaGlblOauth-meta.xml
# (relative to SFDX_DIR / force-app/main/default/extlClntAppGlobalOauthSets/)
RETRIEVED_RELATIVE="force-app/main/default/extlClntAppGlobalOauthSets/SfblE2E.ecaGlblOauth-meta.xml"
RETRIEVED="$SFDX_DIR/$RETRIEVED_RELATIVE"

echo "[discover_eca_consumer_key] retrieving ExtlClntAppGlobalOauthSettings:SfblE2E ..." >&2
(
  cd "$SFDX_DIR"
  sf project retrieve start \
    --metadata "ExtlClntAppGlobalOauthSettings:SfblE2E" \
    --target-org "${E2E_SCRATCH_ORG}" \
    --wait 10
) >&2

if [[ ! -f "$RETRIEVED" ]]; then
  echo "ERROR: metadata retrieve succeeded but expected file not found:" >&2
  echo "  $RETRIEVED" >&2
  echo "  Check that the ECA was deployed to the org before this step runs." >&2
  exit 1
fi

# ── Parse the consumer key from the retrieved XML ────────────────────────────
CONSUMER_KEY="$(python3 - "$RETRIEVED" <<'PYEOF'
import sys, re
from pathlib import Path

xml = Path(sys.argv[1]).read_text()
m = re.search(r'<consumerKey>([^<]+)</consumerKey>', xml)
if not m:
    print("ERROR: <consumerKey> element not found in retrieved XML.", file=sys.stderr)
    print("  This usually means the ECA was never deployed or the retrieve failed silently.", file=sys.stderr)
    print("  Re-run the deploy step and retry.", file=sys.stderr)
    sys.exit(1)

key = m.group(1).strip()
if not key:
    print("ERROR: <consumerKey> element is empty in retrieved XML.", file=sys.stderr)
    sys.exit(1)

print(key)
PYEOF
)"

# ── Sanitise the retrieved file ──────────────────────────────────────────────
# Strip consumerKey and consumerSecret so git-tracked files never contain secrets.
# The certificate (public key, safe to commit) is left intact.
python3 - "$RETRIEVED" <<'PYEOF'
import re, sys
from pathlib import Path
p = Path(sys.argv[1])
xml = p.read_text()
xml = re.sub(r'\s*<consumerKey>[^<]*</consumerKey>', '', xml)
xml = re.sub(r'\s*<consumerSecret>[^<]*</consumerSecret>', '', xml)
p.write_text(xml)
PYEOF

echo "[discover_eca_consumer_key] consumer key discovered and file sanitised." >&2

# ── Emit the key (and ONLY the key) to stdout ─────────────────────────────────
# Callers capture this with $(...) or tee.  The workflow step appends to $GITHUB_ENV.
printf '%s\n' "$CONSUMER_KEY"
