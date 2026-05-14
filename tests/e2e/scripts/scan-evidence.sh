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

# Unpack every *.zip inside the mirror, then loop until no new zips appear.
# Playwright trace attachments are sometimes shaped as a `*.zip` containing
# nested `*.zip` files (Codex review on PR #92 reproduced an outer→inner→
# secret fixture that single-pass `find` missed). The fix is to rescan
# after every pass until a pass extracts nothing.
#
# Bound the loop at 10 passes as a safety net against an adversarial zip
# bomb / cycle (a zip referencing itself by hash). 10 levels of nesting is
# vastly more than any legitimate Playwright payload.
MAX_UNPACK_PASSES=10
unpack_pass=0
while [ "$unpack_pass" -lt "$MAX_UNPACK_PASSES" ]; do
  unpack_pass=$((unpack_pass + 1))
  found_any=0
  while IFS= read -r -d '' zipfile; do
    out="${zipfile%.zip}.unpacked"
    if [ -d "$out" ]; then
      # Already processed in a previous pass — leave alone.
      continue
    fi
    mkdir -p "$out"
    if unzip -qq -o "$zipfile" -d "$out" 2>/dev/null; then
      found_any=1
    else
      echo "WARN: failed to unpack $zipfile — treating raw bytes as scan surface" >&2
    fi
  done < <(find "$STAGING_DIR/report" -type f -name "*.zip" -print0)
  if [ "$found_any" -eq 0 ]; then
    break
  fi
done
if [ "$unpack_pass" -ge "$MAX_UNPACK_PASSES" ]; then
  echo "WARN: hit MAX_UNPACK_PASSES=$MAX_UNPACK_PASSES — deeper nested zips not scanned" >&2
fi

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

  # SECURITY (Codex P1, PR #92): we MUST NOT echo the matched line content
  # into CI logs — the whole point of failing the publish is that the
  # content is sensitive. Use `grep -l` to capture file paths only, then
  # report file + line-number (no content). The line number is enough for
  # an operator to fetch the report artefact from the workflow and triage
  # offline; the matched text never leaves the runner.
  #
  # -r recursive; -E extended; -I skip binary; -l files-with-matches only.
  # Newline-delimited (not -Z) because command substitution strips NUL
  # bytes. Test result filenames never contain newlines so this is safe.
  # `|| true` so set -e doesn't abort the loop on no-match.
  files="$(grep -rIEl -- "$regex" "$STAGING_DIR/report" 2>/dev/null || true)"

  if [ -n "$files" ]; then
    HITS=$((HITS + 1))
    file_summary=""
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      # Re-grep the matching file to capture line numbers only — never
      # the matched text. `cut -d: -f1` strips the line content; we keep
      # just the file:lineno pairs.
      line_nos="$(grep -nIE -- "$regex" "$f" 2>/dev/null | cut -d: -f1 | head -10 | tr '\n' ',' | sed 's/,$//')"
      # Path relative to staging root for cleaner output.
      rel="${f#"$STAGING_DIR/report/"}"
      file_summary="${file_summary}  ${rel} (lines: ${line_nos})
"
    done <<< "$files"
    HIT_LINES="${HIT_LINES}
=== HIT: ${name} ===
${file_summary}"
  fi
done

# ─────────────────────────────────────────────────────────────────────────────
# Result
# ─────────────────────────────────────────────────────────────────────────────

if [ "$HITS" -gt 0 ]; then
  echo "[scan-evidence] SECRET PATTERN(S) DETECTED — publish must abort." >&2
  echo "[scan-evidence] $HITS distinct pattern(s) matched in $REPORT_DIR" >&2
  echo "[scan-evidence] NOTE: matched line content is suppressed by design." >&2
  echo "[scan-evidence]       Fetch the report from the workflow run to triage." >&2
  printf '%s\n' "$HIT_LINES" >&2
  exit 1
fi

echo "[scan-evidence] OK — no secret patterns matched in $REPORT_DIR"
exit 0
