#!/usr/bin/env bash
# publish-evidence.sh — centralized wrapper for publishing Allure test
# evidence to the SFBL-334 dashboard.
#
# SFBL-334 / SFBL-345. Every evidence publish — across tiers, layers,
# branches, PRs — goes through this script. Workflows never call
# `aws s3 sync` against the evidence bucket directly; that's enforced
# structurally by SFBL-347 G's CI lint step, but the design intent is
# captured here: this is the ONE place to add the scanner gate
# (SFBL-347), the size sentinel, the history-merge, and the cache
# invalidation.
#
# Usage:
#   publish-evidence.sh BUCKET DISTRIBUTION_ID PREFIX RESULTS_DIRS...
#
# Args:
#   BUCKET          - S3 bucket name (e.g. bulkloader-testevidence-...)
#   DISTRIBUTION_ID - CloudFront distribution ID for invalidation
#   PREFIX          - In-bucket prefix without leading/trailing slash
#                     (e.g. "pr-123", "main", "tier-2/run-456")
#   RESULTS_DIRS    - One or more allure-results directories to merge
#                     and publish. At least one required.
#
# Steps (in order):
#   1. Merge all RESULTS_DIRS into a single staging directory
#   2. Pull prior history/ from s3://${BUCKET}/${PREFIX}/history/
#      (ignore-failure for the first run on a new prefix)
#   3. Run `allure generate` against the merged staging dir
#   4. Size sentinel: fail if the generated report exceeds 50 MB
#   5. Scanner placeholder — SFBL-347 G wires the real call here
#   6. `aws s3 sync` the generated report to s3://${BUCKET}/${PREFIX}/
#   7. CloudFront invalidation for /${PREFIX}/*
#
# Exit codes:
#   0 = published successfully
#   non-zero = any step failed; report not published

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Args + validation
# ─────────────────────────────────────────────────────────────────────────────

if [ "$#" -lt 4 ]; then
  cat >&2 <<EOF
Usage: $0 BUCKET DISTRIBUTION_ID PREFIX RESULTS_DIRS...
  e.g. $0 bulkloader-testevidence-... E25UFH1IGQN249 pr-123 \\
           tests/e2e/allure-results backend/allure-results
EOF
  exit 64  # EX_USAGE
fi

BUCKET="$1"
DISTRIBUTION_ID="$2"
PREFIX="$3"
shift 3
RESULTS_DIRS=("$@")

# Strip leading/trailing slashes off PREFIX defensively — the script is
# agnostic to whether the caller passed "pr-123" or "/pr-123/".
PREFIX="${PREFIX#/}"
PREFIX="${PREFIX%/}"

if [ -z "$PREFIX" ]; then
  echo "ERROR: PREFIX must not be empty" >&2
  exit 1
fi

# ─────────────────────────────────────────────────────────────────────────────
# Optional history prefix override (SFBL-348)
# ─────────────────────────────────────────────────────────────────────────────
# Tier 2 publishes use a per-run `tier-2/{run-id}/` prefix but want a SHARED
# trend across runs, so callers can override the history location via the
# HISTORY_PREFIX env var. Default: `${PREFIX}/history` (unchanged for Tier
# 1a/1b/backend publishes which all want per-prefix history).
#
# When set, the wrapper:
#   - Pulls prior history from s3://${BUCKET}/${HISTORY_PREFIX}/ (Step 2)
#   - After Step 6's main sync, additionally pushes the freshly generated
#     ${REPORT_DIR}/history/ back to the same path so the next run sees it.
#   - Extends the CloudFront invalidation to /${HISTORY_PREFIX}/* as well.
HISTORY_PREFIX="${HISTORY_PREFIX:-${PREFIX}/history}"
HISTORY_PREFIX="${HISTORY_PREFIX#/}"
HISTORY_PREFIX="${HISTORY_PREFIX%/}"

# Working directory under the system temp root, cleaned up on exit. Using
# mktemp here so concurrent runs can't collide on the same `./staging/`
# in CI workspace.
STAGING_DIR="$(mktemp -d -t sfbl-evidence-staging.XXXXXX)"
REPORT_DIR="$(mktemp -d -t sfbl-evidence-report.XXXXXX)"
cleanup() { rm -rf "$STAGING_DIR" "$REPORT_DIR"; }
trap cleanup EXIT

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Merge inputs
# ─────────────────────────────────────────────────────────────────────────────
echo "[publish-evidence] Merging $# results dirs into $STAGING_DIR"
for dir in "${RESULTS_DIRS[@]}"; do
  if [ ! -d "$dir" ]; then
    echo "  (skipping $dir — not a directory)" >&2
    continue
  fi
  # Use cp -R rather than rsync to keep zero npm/system deps.
  # The trailing /. preserves the contents-of-dir semantics across
  # GNU and BSD cp.
  cp -R "$dir"/. "$STAGING_DIR"/
  echo "  ✓ merged $dir"
done

if ! ls -A "$STAGING_DIR" >/dev/null 2>&1; then
  echo "ERROR: staging dir is empty after merge — no allure-results to publish" >&2
  exit 1
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Pull prior history (from HISTORY_PREFIX — shared for Tier 2, per-
# prefix for everything else; see env-var block above)
# ─────────────────────────────────────────────────────────────────────────────
echo "[publish-evidence] Pulling prior history from s3://${BUCKET}/${HISTORY_PREFIX}/"
mkdir -p "$STAGING_DIR/history"
# `|| true` because the first publish to a fresh prefix has no history yet.
aws s3 sync "s3://${BUCKET}/${HISTORY_PREFIX}/" "$STAGING_DIR/history/" --no-progress || true

# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Generate report
# ─────────────────────────────────────────────────────────────────────────────
echo "[publish-evidence] Generating Allure report → $REPORT_DIR"
allure generate "$STAGING_DIR" --clean -o "$REPORT_DIR"

# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Size sentinel (50 MB quota guard)
# ─────────────────────────────────────────────────────────────────────────────
SIZE_LIMIT_MB=50
SIZE_LIMIT_BYTES=$((SIZE_LIMIT_MB * 1024 * 1024))

# `du -sb` is GNU-only; BSD/macOS `du` uses `-A` for apparent bytes via 1K blocks.
# Compute in a portable way: total bytes via `find ... -printf` is GNU-only too;
# the most portable cross-platform path is `wc -c` summed over files.
SIZE_BYTES=$(find "$REPORT_DIR" -type f -exec wc -c {} + | awk '/total$/ {print $1}' | tail -1)
SIZE_BYTES="${SIZE_BYTES:-0}"
SIZE_MB=$((SIZE_BYTES / 1024 / 1024))

echo "[publish-evidence] Report size: ${SIZE_BYTES} bytes (~${SIZE_MB} MB), limit ${SIZE_LIMIT_MB} MB"
if [ "$SIZE_BYTES" -gt "$SIZE_LIMIT_BYTES" ]; then
  echo "ERROR: report size ${SIZE_BYTES} bytes exceeds ${SIZE_LIMIT_MB} MB quota guard." >&2
  echo "       Investigate before raising the limit — bucket retention isn't unlimited." >&2
  exit 1
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Canary + secret scanner (SFBL-347 G)
# ─────────────────────────────────────────────────────────────────────────────
# Fail-closed gate. Scanner is the structural backstop behind the redactor
# (SFBL-346 F): if a credential pattern made it past the post-test redactor
# into the generated report, this step blocks the publish.
#
# CANARY_TOKEN is inherited from the environment when CI is exercising the
# fail-closed lane (.github/workflows/canary-evidence-scan.yml). For normal
# publishes the env is unset and only the generic secret patterns apply.
echo "[publish-evidence] Running canary + secret scanner over $REPORT_DIR"
SCANNER_PATH="$(dirname "$0")/scan-evidence.sh"
if [ ! -x "$SCANNER_PATH" ]; then
  echo "ERROR: scanner script not found or not executable: $SCANNER_PATH" >&2
  exit 1
fi
"$SCANNER_PATH" "$REPORT_DIR"

# ─────────────────────────────────────────────────────────────────────────────
# Step 6: S3 sync
# ─────────────────────────────────────────────────────────────────────────────
echo "[publish-evidence] Syncing report → s3://${BUCKET}/${PREFIX}/"
aws s3 sync "$REPORT_DIR"/ "s3://${BUCKET}/${PREFIX}/" --delete --no-progress

# ─────────────────────────────────────────────────────────────────────────────
# Step 6b: Push history back to HISTORY_PREFIX when it differs from
# ${PREFIX}/history (i.e. when the caller opted into shared-history mode).
# Without this, Tier 2's next run pulls an empty history dir and the trend
# graph never builds.
# ─────────────────────────────────────────────────────────────────────────────
if [ "$HISTORY_PREFIX" != "${PREFIX}/history" ]; then
  if [ -d "$REPORT_DIR/history" ]; then
    echo "[publish-evidence] Pushing fresh history → s3://${BUCKET}/${HISTORY_PREFIX}/"
    aws s3 sync "$REPORT_DIR/history/" "s3://${BUCKET}/${HISTORY_PREFIX}/" --delete --no-progress
  else
    # Should never happen — `allure generate` always writes history/.
    # Belt-and-braces in case future Allure versions change layout.
    echo "[publish-evidence] WARN: report had no history/ subdir; skipping shared-history push" >&2
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 7: CloudFront invalidation
# ─────────────────────────────────────────────────────────────────────────────
# Always invalidate the main prefix. When HISTORY_PREFIX differs (Tier 2),
# also invalidate that path so the next viewer's browser sees the new trend.
if [ "$HISTORY_PREFIX" != "${PREFIX}/history" ]; then
  echo "[publish-evidence] Creating CloudFront invalidation for /${PREFIX}/* + /${HISTORY_PREFIX}/*"
  aws cloudfront create-invalidation \
    --distribution-id "$DISTRIBUTION_ID" \
    --paths "/${PREFIX}/*" "/${HISTORY_PREFIX}/*" \
    --query 'Invalidation.{Id:Id,Status:Status}' \
    --output table
else
  echo "[publish-evidence] Creating CloudFront invalidation for /${PREFIX}/*"
  aws cloudfront create-invalidation \
    --distribution-id "$DISTRIBUTION_ID" \
    --paths "/${PREFIX}/*" \
    --query 'Invalidation.{Id:Id,Status:Status}' \
    --output table
fi

echo "[publish-evidence] ✓ Published to s3://${BUCKET}/${PREFIX}/"
echo "[publish-evidence] ✓ URL: https://reports.bulkloader.forcetide.net/${PREFIX}/"
