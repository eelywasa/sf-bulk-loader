#!/usr/bin/env bash
#
# Run Tier 1b E2E specs locally against a fixture-mode Docker stack.
#
# Purpose
# -------
# CI feedback for Tier 1b takes ~10-13 minutes per push (image build +
# stack-up + Playwright run).  This script mirrors the same Tier 1b
# topology on your laptop so you can iterate on spec/page-object changes
# with a sub-minute loop after the first build.
#
# Usage
# -----
#   ./tests/e2e/scripts/run-tier-1b-local.sh                # headless
#   ./tests/e2e/scripts/run-tier-1b-local.sh --headed       # see the browser
#   ./tests/e2e/scripts/run-tier-1b-local.sh --ui           # Playwright UI mode
#   ./tests/e2e/scripts/run-tier-1b-local.sh --no-rebuild   # skip docker build
#   ./tests/e2e/scripts/run-tier-1b-local.sh --keep-up      # don't tear down
#
# The first run builds the backend + frontend images (slow).  Subsequent
# runs hit your local Docker layer cache — much faster than GHA's.  Pass
# --no-rebuild to skip the `docker compose build` step entirely if you
# know nothing image-relevant changed.
#
# What this does
# --------------
# 1. Writes a temp .env at repo root with APP_DISTRIBUTION=desktop and
#    SF_DESCRIBE_FIXTURES_DIR pointed at the in-container fixture paths.
# 2. Creates data/{input,output,db} if missing.
# 3. (Optionally) builds the images.
# 4. Brings up the stack with the same three compose files CI uses:
#    docker-compose.yml + .ci.yml + .e2e-tier-1b.yml.
# 5. Runs `npm run e2e:1b` (or whatever Playwright invocation flags say).
# 6. Tears down with `down -v` unless --keep-up is set.
#
# This script restores any pre-existing .env on exit so it doesn't
# trample your local-dev configuration.

set -euo pipefail

# Locate repo root (assumes script lives at tests/e2e/scripts/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

# ── Flag parsing ──────────────────────────────────────────────────────
REBUILD=1
KEEP_UP=0
PW_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-rebuild) REBUILD=0; shift ;;
    --keep-up)    KEEP_UP=1; shift ;;
    --headed|--ui|--debug) PW_ARGS+=("$1"); shift ;;
    -h|--help)
      sed -n '/^# Usage/,/^$/p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) PW_ARGS+=("$1"); shift ;;
  esac
done

COMPOSE_FILES=(
  -f docker-compose.yml
  -f docker-compose.ci.yml
  -f docker-compose.e2e-tier-1b.yml
)

# ── .env handling ─────────────────────────────────────────────────────
ENV_BACKUP=""
if [[ -f .env ]]; then
  ENV_BACKUP=".env.tier-1b-local-backup.$$"
  cp .env "$ENV_BACKUP"
  echo "→ Backed up existing .env to $ENV_BACKUP"
fi

cat > .env <<'EOF'
APP_DISTRIBUTION=desktop
SF_DESCRIBE_FIXTURES_DIR=/data/e2e-fixtures/app:/data/e2e-fixtures/sf
EOF

# ── Cleanup trap ──────────────────────────────────────────────────────
cleanup() {
  local exit_code=$?
  if [[ "$KEEP_UP" -eq 0 ]]; then
    echo "→ Tearing down stack…"
    docker compose "${COMPOSE_FILES[@]}" down -v >/dev/null 2>&1 || true
  else
    echo "→ --keep-up set; stack left running.  Tear down with:"
    echo "  docker compose ${COMPOSE_FILES[*]} down -v"
  fi
  if [[ -n "$ENV_BACKUP" ]]; then
    mv "$ENV_BACKUP" .env
    echo "→ Restored original .env"
  else
    rm -f .env
  fi
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

# ── Data dirs ─────────────────────────────────────────────────────────
mkdir -p data/input data/output data/db

# ── Build ─────────────────────────────────────────────────────────────
if [[ "$REBUILD" -eq 1 ]]; then
  echo "→ Building backend + frontend images (first run is slow)…"
  docker compose "${COMPOSE_FILES[@]}" build
fi

# ── Stack up ──────────────────────────────────────────────────────────
echo "→ Bringing up Tier 1b stack…"
docker compose "${COMPOSE_FILES[@]}" up -d --wait --timeout 120

# ── Sanity check fixture mode is active ───────────────────────────────
HEALTH="$(curl -sf http://localhost/api/health || true)"
if [[ -z "$HEALTH" ]]; then
  echo "✗ Backend health check failed — is anything else bound to :80?" >&2
  exit 1
fi
echo "→ Backend health: $HEALTH"

# ── Playwright ────────────────────────────────────────────────────────
echo "→ Running Tier 1b specs…"
cd tests/e2e
export E2E_BASE_URL="http://localhost"
if [[ "${#PW_ARGS[@]}" -gt 0 ]]; then
  npx playwright test --project=tier-1b "${PW_ARGS[@]}"
else
  npm run e2e:1b
fi
