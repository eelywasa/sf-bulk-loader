# Mutual TLS (mTLS) Connectivity to Salesforce

**Status:** Live spec — design locked, implementation not started (SFBL-394 epic).
This file describes the proposed design for adding mutual-TLS client-certificate
support to the Salesforce connection. It is **not** archived until all stories
are Done and the epic PR is merged.

---

## Background

The loader authenticates to Salesforce with the OAuth 2.0 **JWT Bearer** flow
(`salesforce_auth.py`): an RS256-signed assertion is POSTed to
`/services/oauth2/token`, and the returned access token is used as a
`Bearer` header on all Bulk API 2.0 and REST calls.

Some Salesforce customers enforce **mutually-authenticated TLS** on inbound API
connections as a defence-in-depth control. When the org enables this, an
API-only user is granted the **"Enforce SSL/TLS Mutual Authentication"**
permission, a client certificate is uploaded to the org's mutual-authentication
certificate store, and the client must then present that certificate during the
TLS handshake — on a dedicated endpoint (**port 8443**).

Crucially, mTLS is **not an alternative auth mechanism**. It sits *underneath*
the existing JWT bearer flow at the transport layer. Once the user has the
permission, **every** connection that user makes must present the client cert —
including the token exchange itself, not just the data API. The loader keeps the
entire JWT flow unchanged and simply adds a client certificate to the TLS
handshake on every Salesforce-bound request.

References:
- [Set Up a Mutual Authentication Certificate for API Login](https://help.salesforce.com/s/articleView?language=en_US&id=sf.security_keys_uploading_mutual_auth_cert.htm&type=5)
- [Certificates in Mutual Authentication for Salesforce](https://help.salesforce.com/s/articleView?id=000383575&language=en_US&type=1)

---

## Design decisions

### D1 — Per-connection, opt-in (locked)

mTLS is configured per `Connection`, gated by a `mutual_auth_enabled` boolean
(default `false`). Rationale: the loader is connection-centric — different orgs
may require different certs (or none), and a global toggle could not express
that. mTLS is orthogonal to the distribution profile (`desktop`,
`self_hosted`, `aws_hosted`) — no profile gating.

### D2 — Storage: two encrypted PEM columns + flag + transport port (locked)

New `Connection` columns:

```sql
ALTER TABLE connection ADD COLUMN mutual_auth_enabled BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE connection ADD COLUMN client_cert_pem TEXT NULL;   -- Fernet-encrypted at rest
ALTER TABLE connection ADD COLUMN client_key_pem  TEXT NULL;   -- Fernet-encrypted at rest
ALTER TABLE connection ADD COLUMN mutual_auth_port INTEGER NULL; -- default 8443 when enabled
```

- Cert and key are stored as **separate** PEM strings, both encrypted with the
  existing Fernet helper (`encrypt_secret` / `decrypt_secret`), reusing the
  exact pattern already used for `private_key`. Separate fields (vs a combined
  bundle) keep validation and rotation independent.
- The cert private key (`client_key_pem`) must be **unencrypted PEM** (no
  passphrase) before storage — Fernet-at-rest is the protection layer, matching
  how the JWT signing key is handled. Passphrase-protected keys are rejected at
  write time with a clear message.
- `mutual_auth_port` defaults to `8443` (the Salesforce mTLS port) when enabled;
  stored explicitly so a future endpoint change does not require a code change.

### D3 — Endpoint vs `aud` decoupling (locked) + port behaviour (spike-gated)

The JWT `aud` claim and the OAuth/REST **base URLs** stay **canonical**
(`https://login.salesforce.com`, the `*.my.salesforce.com` instance host) —
these are stored in `login_url` / `instance_url` and are **never** rewritten.

mTLS only changes the **transport target**: when `mutual_auth_enabled`, requests
connect to the same host on `mutual_auth_port` (8443). This is expressed by
building the request URL with the port appended, while `_build_jwt` continues to
use the unmodified `login_url` for `aud`.

> ⚠️ **Falsification risk:** rewriting `login_url` to `:8443` and reusing it for
> the `aud` claim would silently break the assertion (Salesforce rejects a
> non-canonical `aud`). The implementation MUST keep `aud` canonical; a unit
> test asserts the `aud` claim equals the stored `login_url` even when
> `mutual_auth_enabled` and a custom port are set.

**Spike-gated (S1):** the exact endpoint/port URL format must be confirmed
against a real org with the permission enabled before S3 is finalised:
- Does the token endpoint (`login.salesforce.com` / `test.salesforce.com` /
  My Domain) accept the client cert on `:8443`, or only the instance host?
- Is the URL form `https://<host>:8443/services/...` exactly, or a distinct
  mutual-auth subdomain?
- Confirm both the token exchange **and** the instance API require the cert.

S1 produces the answers that finalise D3; record them as a `DECISIONS.md` entry.

### D4 — Central SSL-context factory, not per-call params (locked)

There are **seven** Salesforce HTTP egress sites today:

| Site | Purpose |
|---|---|
| `salesforce_auth.py:226` | JWT token exchange — **must** carry the cert |
| `salesforce_bulk.py:117` | Bulk API 2.0 job lifecycle (owned client) |
| `connections.py:221` | List SObjects (`/sobjects/`) |
| `connections.py:251` | Test connection |
| `load_steps.py:299,366` | Step preview / describe |
| `salesforce_query_validation.py:247` | SOQL validation |
| `bulk_query_executor.py:291` | Bulk Query (SFBL-114) |

Threading `cert=`/`verify=` through each is error-prone and the token-exchange
site is the easiest to forget. Instead introduce a single helper:

```python
# app/services/sf_transport.py  (new)
def build_sf_ssl_context(connection: Connection) -> ssl.SSLContext | None: ...
def sf_request_url(connection: Connection, base_url: str, path: str) -> str: ...
```

- Returns `None` when `mutual_auth_enabled` is false (httpx uses its default
  verification — behaviour identical to today).
- When enabled, builds an `ssl.SSLContext` loaded with the decrypted client
  cert + key, and `sf_request_url` appends `mutual_auth_port`.
- Every egress site constructs its `httpx.AsyncClient` via the factory and
  builds URLs via `sf_request_url`. `SalesforceBulkClient` accepts the prebuilt
  context (its `http_client` injection seam already exists).

### D5 — In-memory cert → SSLContext (implementation gotcha, locked)

`ssl.SSLContext.load_cert_chain()` reads cert/key from **file paths**, not
in-memory strings — and httpx's `cert=` does the same. Since the PEMs live
encrypted in the DB, the factory must bridge memory → SSLContext. Approach:

1. Decrypt cert + key.
2. Write to a `0600`-permissioned temp file (or per-process temp dir), build the
   `SSLContext` via `load_cert_chain`, then unlink immediately.
3. **Cache** the resulting `SSLContext` keyed by `(connection_id, cert
   fingerprint)` so the decrypt-and-write dance happens once per cert version,
   not per request. Invalidate on connection update.

The temp file exists only for the microseconds of `load_cert_chain`; it is never
left on disk. (If a future Python/cryptography in-memory path is available it
supersedes the tempfile, but the tempfile approach is the portable baseline.)

### D6 — Write-time validation with falsification (locked)

On create/update, when `mutual_auth_enabled` is true:

- `client_cert_pem` and `client_key_pem` are **required** (422 if missing).
- Both must parse as PEM (via `cryptography`); a passphrase-protected key → 422.
- The key must **match** the certificate (public key of the cert equals the
  public key derived from the private key) → mismatch is **422**.
- The cert's `notAfter` is surfaced (warning if already expired; not a hard
  reject — operators may stage certs).

> **Falsification clause:** a test supplies a valid cert with a **mismatched**
> key and asserts the API returns 422. If the match check were dropped, this
> test fails. A second test supplies a matched pair and asserts 201/200.

### D7 — Security & redaction (locked)

- `client_key_pem` (and `client_cert_pem`) are **never** returned in any API
  response — same treatment as `private_key` / `access_token`. They are absent
  from both `ConnectionPublic` and `ConnectionResponse`.
- A read-only **derived** view is exposed for the UI: cert fingerprint (SHA-256),
  subject CN, and `notAfter` expiry — computed from the stored cert, never the
  raw PEM. This lets operators confirm *which* cert is loaded without exposing it.
- Error/handshake-failure messages are truncated and never echo key material
  (consistent with the SFBL-60 sanitisation rules already applied in
  `_exchange_jwt`).

### D8 — Test-connection is the acceptance proof (locked)

The existing `POST /api/connections/{id}/test` exercises the full path
(token exchange + a live `/sobjects/` call). With mTLS enabled it becomes the
end-to-end proof: a successful test against a real mutual-auth org confirms the
cert is presented correctly on both the token and data endpoints. Tier 2
(real-scratch-org) coverage is out of standard epic DoD and rides the E2E
enabler's schedule.

---

## Observability (folded into S3, per Observability DoD)

- New outcome code `OutcomeCode.SF_MTLS_HANDSHAKE_FAILED` for TLS-layer
  failures (cert rejected / not presented), distinct from auth (`401`) failures.
- `auth.token.acquire` and connection-test events carry a `mutual_auth: bool`
  field in `extra={}` so logs show whether mTLS was in play.
- No new span/metric — the handshake is inside existing request scopes.

---

## UI (S4)

`ConnectionForm` gains a **"Mutual TLS"** section, collapsed by default:

- A `mutual_auth_enabled` toggle.
- When on: client-certificate (PEM) and client-key (PEM) upload/paste fields,
  and an optional port field (default 8443, pre-filled).
- The cert/key fields are write-only — on edit they show "•••• (set)" with a
  Replace action, never the stored value (mirrors the private-key field).
- A read-only badge showing the derived fingerprint + expiry (from D7) once a
  cert is stored, with an expiry warning chip when near/after `notAfter`.
- The existing **Test connection** button is the verification affordance.

---

## Out of scope

| Item | Status |
|---|---|
| Global / org-wide single cert config | Deferred — per-connection only (D1) |
| Automated cert rotation / renewal | Deferred — manual replace via UI |
| OCSP / CRL revocation checking | Deferred — relies on SF-side validation |
| mTLS for non-SF egress (S3, SMTP, webhooks) | Out of scope — SF connection only |
| Passphrase-protected client keys | Rejected at write time (D6) |

---

## Story breakdown (one bundled epic PR)

This is a single coherent feature, so it ships as **one shippable PR** on a
`feat/sfbl-<epic>-mutual-tls` branch (per CLAUDE.md epic rule), with each story
tracked individually in Jira.

| Story | Ticket | Scope |
|---|---|---|
| **S1 — Spike: validate SF mTLS endpoint behaviour** | SFBL-395 | Against a real org with the "Enforce SSL/TLS Mutual Authentication" permission, confirm: token vs instance endpoint cert requirement, exact `:8443` URL form, and that the canonical `aud` is still accepted. **Owner-gated** (needs an org + cert provisioned). Output: a `DECISIONS.md` entry finalising D3. |
| **S2 — Data model, schema & validation** | SFBL-396 | Alembic migration (D2 columns), `Connection` model fields, Pydantic schema additions, encrypt-on-write wiring, write-time cert/key validation incl. mismatch 422 (D6), redaction (D7), derived fingerprint/expiry view. |
| **S3 — SSL-context factory & egress wire-in + observability** | SFBL-397 | New `sf_transport.py` (D4/D5): SSLContext builder with tempfile + fingerprint cache, `sf_request_url` port handling, `aud`-decoupling guard (D3). Thread through all seven egress sites. Observability outcome code + `mutual_auth` log field. |
| **S4 — UI** | SFBL-398 | `ConnectionForm` Mutual-TLS section, write-only cert/key fields, fingerprint/expiry badge, Tier 1a spec coverage. |
| **S5 — Docs** | SFBL-399 | This spec finalised, `docs/usage/connections.md` mTLS section, deployment note, `.env.example` if any new config, `DECISIONS.md` entry, archive spec on merge. |

### Dependencies

```
S1 (spike) ──▶ S3 (finalises endpoint/port)
S2 (model) ──▶ S3 (factory reads the new fields) ──▶ S4 (UI), S5 (docs)
```

S1 and S2 can run in parallel; S3 depends on both; S4/S5 follow S3.
