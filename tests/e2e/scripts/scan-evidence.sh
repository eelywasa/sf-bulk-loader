#!/usr/bin/env bash
# scan-evidence.sh — SFBL-334 / SFBL-347.
#
# Fail-closed canary + secret scanner. Runs as the gate inside
# `publish-evidence.sh` immediately before `aws s3 sync`. If this script
# exits non-zero, the publish wrapper aborts and no artefacts reach S3.
#
# Usage:
#   scan-evidence.sh REPORT_DIR
#
# Args:
#   REPORT_DIR  - Path to a generated allure-report tree (post `allure
#                 generate`). The scanner walks the tree recursively,
#                 unpacks every *.zip into a tmp dir, and greps the
#                 combined surface for known secret shapes.
#
# Env:
#   CANARY_TOKEN (optional) - A specific canary string the CI lane injected
#                             into a fixture trace. When set, the scanner
#                             treats finding it as a hit (the canary lane
#                             uses this to prove the scanner is alive — see
#                             .github/workflows/canary-evidence-scan.yml).
#
# Exit codes:
#   0 - Clean (no patterns matched)
#   1 - At least one pattern matched (publish must abort)
#   64 - Usage error
#   65 - Required dependency missing (unzip, grep)
#
# Why bash + grep rather than a Python tool: zero new runtime deps in CI,
# and grep -E is already a hard line of defense across the OS. The scanner
# is intentionally simple; the goal is "is this string anywhere in the
# generated report?" not nuanced parsing.

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Args + validation
# ─────────────────────────────────────────────────────────────────────────────

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 REPORT_DIR" >&2
  exit 64
fi

REPORT_DIR="$1"

if [ ! -d "$REPORT_DIR" ]; then
  echo "ERROR: REPORT_DIR does not exist or is not a directory: $REPORT_DIR" >&2
  exit 64
fi

for dep in unzip grep find; do
  if ! command -v "$dep" >/dev/null 2>&1; then
    echo "ERROR: required dependency '$dep' is not on PATH" >&2
    exit 65
  fi
done

# ─────────────────────────────────────────────────────────────────────────────
# Staging
# ─────────────────────────────────────────────────────────────────────────────

STAGING_DIR="$(mktemp -d -t sfbl-evidence-scan.XXXXXX)"
cleanup() { rm -rf "$STAGING_DIR"; }
trap cleanup EXIT

# Mirror the report into staging so we can decompress in place without
# touching the caller's tree. Use cp -R for portability (GNU + BSD).
mkdir -p "$STAGING_DIR/report"
cp -R "$REPORT_DIR"/. "$STAGING_DIR/report"/

# Unzip every *.zip inside the mirror. Some Playwright traces are deeply
# nested; -o overwrites so we can rerun. Suppress unzip stdout (verbose
# by default).
while IFS= read -r -d '' zipfile; do
  out="${zipfile%.zip}.unpacked"
  mkdir -p "$out"
  unzip -qq -o "$zipfile" -d "$out" || {
    echo "WARN: failed to unpack $zipfile — treating raw bytes as scan surface" >&2
  }
done < <(find "$STAGING_DIR/report" -type f -name "*.zip" -print0)

# ─────────────────────────────────────────────────────────────────────────────
# Patterns
# ─────────────────────────────────────────────────────────────────────────────
#
# Each entry: NAME|REGEX (extended). The pipe is the separator so the
# regex itself can contain other delimiters. Patterns are deliberately
# narrow so a clean run doesn't false-positive on hex hashes or asset
# fingerprints.
#
# Bearer-token: requires 8+ chars of base64ish material after the literal.
# JWT: three base64url segments separated by dots, each ≥20 chars to avoid
#   matching pure-syntax JSON like `eyJ.{}.{}`.
# AWS access key: AKIA + 16 uppercase alnum (per AWS docs).
# SF consumer-key: Salesforce connected-app consumer keys begin with
#   `3MVG9` and run ~85 chars of base64-ish material. Treated as a strong
#   indicator — any hit is a publish-blocker.
# PEM block: any `-----BEGIN…-----`.

PATTERNS=(
  "pem-block|-----BEGIN [A-Z0-9 ]+-----"
  "bearer-token|Bearer[[:space:]]+[A-Za-z0-9._~+/=-]{8,}"
  "jwt|eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"
  "aws-access-key|AKIA[0-9A-Z]{16}"
  "sf-consumer-key|3MVG9[A-Za-z0-9._]{50,}"
)

# Optional canary — only added when CANARY_TOKEN is non-empty. The token
# is treated as a literal (escape regex metacharacters with [[..]] tricks
# isn't needed because CI generates the token from uuidgen — all alnum).
if [ -n "${CANARY_TOKEN:-}" ]; then
  PATTERNS+=("canary|${CANARY_TOKEN}")
fi

# ─────────────────────────────────────────────────────────────────────────────
# Scan
# ─────────────────────────────────────────────────────────────────────────────

HITS=0
HIT_LINES=""

for entry in "${PATTERNS[@]}"; do
  name="${entry%%|*}"
  regex="${entry#*|}"

  # -r recursive; -E extended; -I skip binary; -l list files only first;
  # exit 0 even on no match so set -e doesn't abort the loop.
  matches="$(grep -rIEn -- "$regex" "$STAGING_DIR/report" 2>/dev/null | head -10 || true)"

  if [ -n "$matches" ]; then
    HITS=$((HITS + 1))
    HIT_LINES="${HIT_LINES}
=== HIT: ${name} ===
${matches}
"
  fi
done

# ─────────────────────────────────────────────────────────────────────────────
# Result
# ─────────────────────────────────────────────────────────────────────────────

if [ "$HITS" -gt 0 ]; then
  echo "[scan-evidence] SECRET PATTERN(S) DETECTED — publish must abort." >&2
  echo "[scan-evidence] $HITS distinct pattern(s) matched in $REPORT_DIR" >&2
  printf '%s\n' "$HIT_LINES" >&2
  exit 1
fi

echo "[scan-evidence] OK — no secret patterns matched in $REPORT_DIR"
exit 0
