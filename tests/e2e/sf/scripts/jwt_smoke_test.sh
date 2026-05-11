#!/usr/bin/env bash
# JWT bearer grant smoke test for the SfblE2E ECA.
#
# WHY a direct HTTP call (not `sf org login jwt`):
#   The bulk loader's salesforce_auth.py uses the standard OAuth 2.0 JWT bearer
#   grant — a direct HTTPS POST to <instance_url>/services/oauth2/token.  This
#   script replicates exactly that flow so a failure here means a failure in the
#   actual bulk loader auth path, not just in the Salesforce CLI.
#
# WHY expires_in is not asserted:
#   Salesforce JWT bearer token responses do NOT include expires_in.  Token
#   lifetime is controlled by the org's session settings.  Any assertion on
#   expires_in presence would be a false negative.
#
# Output discipline (spec M7 — no secrets leak):
#   Success: JWT smoke test PASSED (issued_at=<utc>, token_type=<type>, expires_in=session-controlled)
#   Failure: masked body with PEM blocks, JWT segments, and long tokens redacted
#   NEVER prints the access token, consumer key, private key, or any other secret.
#
# Usage:
#   ./jwt_smoke_test.sh
#
# Environment variables (all required):
#   E2E_SCRATCH_ORG               — scratch org alias (used to resolve instance URL + username)
#   E2E_BULK_LOADER_CONSUMER_KEY  — OAuth consumer key (from discover_eca_consumer_key.sh)
#   SFBL_E2E_BULK_LOADER_JWT_KEY  — PEM private key contents (GH secret, never a file path)
#
# Exit codes:
#   0 — JWT grant succeeded; bulk loader auth path is functional
#   1 — JWT grant failed; inspect stderr for masked error details

set -euo pipefail

# ── Validate required env vars ───────────────────────────────────────────────
: "${E2E_SCRATCH_ORG:?E2E_SCRATCH_ORG must be set (scratch org alias)}"
: "${E2E_BULK_LOADER_CONSUMER_KEY:?E2E_BULK_LOADER_CONSUMER_KEY must be set}"
: "${SFBL_E2E_BULK_LOADER_JWT_KEY:?SFBL_E2E_BULK_LOADER_JWT_KEY must be set (PEM string)}"

# ── Resolve scratch org instance URL and admin username via the CLI ───────────
echo "[jwt_smoke_test] resolving org details for: ${E2E_SCRATCH_ORG}" >&2
ORG_JSON="$(sf org display --target-org "${E2E_SCRATCH_ORG}" --json)"

INSTANCE_URL="$(printf '%s' "$ORG_JSON" | python3 -c "
import sys, json
d = json.load(sys.stdin)['result']
print(d['instanceUrl'].rstrip('/'))
")"

ADMIN_USERNAME="$(printf '%s' "$ORG_JSON" | python3 -c "
import sys, json
d = json.load(sys.stdin)['result']
print(d['username'])
")"

echo "[jwt_smoke_test] instance_url : ${INSTANCE_URL}" >&2
echo "[jwt_smoke_test] username     : ${ADMIN_USERNAME}" >&2

# ── Write private key to a temp file (in-memory PEM → disk for openssl) ──────
# The key material lives in $SFBL_E2E_BULK_LOADER_JWT_KEY as a PEM string.
# Python's cryptography library (used below) needs it accessible by path.
KEY_FILE="$(mktemp)"
# shellcheck disable=SC2064
trap "rm -f '${KEY_FILE}'" EXIT
printf '%s' "${SFBL_E2E_BULK_LOADER_JWT_KEY}" > "$KEY_FILE"
chmod 600 "$KEY_FILE"

# ── Perform the JWT bearer grant via Python ───────────────────────────────────
# Python handles JWT construction + HTTP — mirrors salesforce_auth.py exactly.
python3 - \
  "${E2E_BULK_LOADER_CONSUMER_KEY}" \
  "${ADMIN_USERNAME}" \
  "${INSTANCE_URL}" \
  "${KEY_FILE}" <<'PYEOF'
import sys, json, time, re, urllib.request, urllib.parse, base64

consumer_key, username, instance_url, key_file = sys.argv[1:]
token_url = f"{instance_url}/services/oauth2/token"

# ── Build and sign the JWT ────────────────────────────────────────────────────
iat = int(time.time())
exp = iat + 180  # 180 s JWT lifetime — Salesforce enforced ceiling
payload = {
    "iss": consumer_key,
    "sub": username,
    "aud": instance_url,
    "exp": exp,
}

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _json_b64url(obj: dict) -> str:
    return _b64url(json.dumps(obj, separators=(",", ":")).encode())

header_enc = _json_b64url({"alg": "RS256", "typ": "JWT"})
payload_enc = _json_b64url(payload)
signing_input = f"{header_enc}.{payload_enc}".encode()

# Try cryptography first (preferred), fall back to PyJWT.
try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

    with open(key_file, "rb") as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)
    sig = private_key.sign(signing_input, asym_padding.PKCS1v15(), hashes.SHA256())
    assertion = f"{signing_input.decode()}.{_b64url(sig)}"
except ImportError:
    try:
        import jwt as pyjwt
        with open(key_file, "rb") as f:
            private_key_bytes = f.read()
        assertion = pyjwt.encode(
            payload, private_key_bytes, algorithm="RS256",
            headers={"typ": "JWT"},
        )
    except ImportError:
        print(
            "ERROR: neither 'cryptography' nor 'PyJWT' is installed.\n"
            "  pip install cryptography",
            file=sys.stderr,
        )
        sys.exit(1)

# ── POST JWT bearer grant ─────────────────────────────────────────────────────
data = urllib.parse.urlencode({
    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
    "assertion": assertion,
}).encode()
req = urllib.request.Request(token_url, data=data, method="POST")
req.add_header("Content-Type", "application/x-www-form-urlencoded")

def _redact(text: str) -> str:
    """Mask PEM blocks, JWT segments, and long opaque tokens from log output."""
    # PEM blocks (-----BEGIN ... -----END-----)
    text = re.sub(
        r"-----BEGIN[^-]+-----[\s\S]+?-----END[^-]+-----",
        "***PEM_REDACTED***",
        text,
    )
    # JWT segments (three base64url parts joined by dots)
    text = re.sub(
        r"[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{10,}",
        "***JWT_REDACTED***",
        text,
    )
    # Long opaque tokens / secrets (>40 chars of base64url or hex)
    text = re.sub(r"[A-Za-z0-9+/=_-]{40,}", "***TOKEN_REDACTED***", text)
    return text

try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode())

        # issued_at is epoch-milliseconds as a string in Salesforce responses.
        raw_issued = result.get("issued_at")
        if raw_issued:
            issued_at_utc = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(raw_issued) / 1000)
            )
        else:
            issued_at_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # Salesforce JWT bearer responses deliberately omit expires_in.
        # Token lifetime is org-session-controlled.  Do NOT assert its presence.
        expires_info = result.get("expires_in", "session-controlled")
        token_type = result.get("token_type", "unknown")
        inst = result.get("instance_url", instance_url)

        print(
            f"JWT smoke test PASSED "
            f"(issued_at={issued_at_utc}, token_type={token_type}, "
            f"expires_in={expires_info})"
        )
        print(f"  instance_url : {inst}")
        sys.exit(0)

except urllib.error.HTTPError as e:
    body = _redact(e.read().decode(errors="replace"))
    print("JWT smoke test FAILED", file=sys.stderr)
    print(
        f"  HTTP {e.code} from {token_url}\n"
        f"  {body}\n"
        "  Common causes:\n"
        "    1. Permission set not assigned (sf org assign permset was skipped)\n"
        "    2. SetupEntityAccess link not created (setup_permset_and_access.sh was skipped)\n"
        "    3. The ECA was not deployed to this scratch org\n"
        "    4. The private key does not match the certificate in the ECA metadata",
        file=sys.stderr,
    )
    sys.exit(1)

except Exception as exc:  # noqa: BLE001
    print(f"JWT smoke test FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
    sys.exit(1)
PYEOF
