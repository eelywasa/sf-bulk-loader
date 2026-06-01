"""Personal Access Token (PAT) issuance and management service (SFBL-366).

Design notes
------------

Token format
~~~~~~~~~~~~
    sfbl_pat_<url-safe-base64(32 random bytes)>

The ``sfbl_pat_`` prefix makes leaked tokens grep-friendly (secret scanning
tools can find them) and instantly identifiable by human reviewers.

Hashing approach
~~~~~~~~~~~~~~~~
PATs are looked up by hash in O(1) via a unique index on ``token_hash``.  This
rules out per-row salted KDFs (bcrypt / Argon2) because:

  1. A per-row salt means the index can only narrow by salt-bucket, not by hash
     value directly — O(log n) at best, not O(1).
  2. Slow KDFs (bcrypt/Argon2) add multi-millisecond latency per-request, which
     is unsuitable for a token authenticated on every API call.

Instead we use a deterministic keyed HMAC-SHA256:

    token_hash = HMAC-SHA256(key=pat_signing_key, msg=plaintext_token).hexdigest()

The ``pat_signing_key`` is derived from ``ENCRYPTION_KEY`` via HKDF-SHA256 with
a fixed context label so it is cryptographically isolated from the Fernet
encryption key:

    pat_signing_key = HKDF(
        algorithm=SHA256,
        length=32,
        salt=None,              # HKDF default (zero-filled salt of hash length)
        info=b"sfbl-pat-hmac-v1",
        key_material=fernet_key_bytes,
    )

The Fernet key is URL-safe base64; we decode it to raw bytes before passing to
HKDF.

Constant-time comparison
~~~~~~~~~~~~~~~~~~~~~~~~
The unique index narrows a lookup to exactly one row (or zero).  After the DB
returns the row we run ``hmac.compare_digest(stored_hash, computed_hash)`` to
confirm the match without leaking timing information about partial string
equality.  This defence-in-depth guard handles adversarial DB results.

Scope (forward-compat)
~~~~~~~~~~~~~~~~~~~~~~
The ``scope`` column on ``PersonalAccessToken`` is accepted by ``issue()`` but
NOT enforced anywhere in v1.  It is serialised as a JSON-compatible string
(e.g. ``'["runs.view", "plans.view"]'``) so the column value is human-readable
in the DB.  Enforcement will be added in a future ticket; this service just
stores whatever the caller supplies.

Out-of-scope items
~~~~~~~~~~~~~~~~~~
- PAT_USED event and last_used_at update → SFBL-367 (auth middleware)
- Management API endpoints (list / create / revoke routes) → SFBL-368
- Scope enforcement → future ticket
"""

from __future__ import annotations

import base64
import hmac
import logging
import secrets
from datetime import datetime, timezone
from hashlib import sha256
from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.personal_access_token import PersonalAccessToken
from app.observability.events import AuthEvent, OutcomeCode

_log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

#: Recognisable token prefix placed before the random entropy.
#: Secret-scanning tools can be configured to alert on this prefix.
TOKEN_PREFIX = "sfbl_pat_"

#: HKDF info label — changing this invalidates all existing token hashes.
#: Bump this constant (and the migration) if the hashing scheme ever changes.
_HKDF_INFO = b"sfbl-pat-hmac-v1"

#: Number of random bytes in the entropy portion of the token.
#: 32 bytes → 43-char URL-safe base64 suffix (before the prefix).
_TOKEN_ENTROPY_BYTES = 32


# ── Key derivation ────────────────────────────────────────────────────────────

def _derive_signing_key(encryption_key: str) -> bytes:
    """Derive a 32-byte HMAC signing key from ``encryption_key``.

    ``encryption_key`` is a Fernet-generated URL-safe base64 string (44 chars
    including padding).  We decode it to raw bytes and pass through HKDF-SHA256
    so the PAT signing key is cryptographically separate from the Fernet key
    that encrypts private keys at rest.

    Args:
        encryption_key: The value of ``settings.encryption_key`` — the raw
            string (URL-safe base64, no newlines).

    Returns:
        32-byte signing key for HMAC-SHA256.
    """
    # Fernet keys are URL-safe base64; decode to raw bytes
    try:
        key_bytes = base64.urlsafe_b64decode(encryption_key)
    except Exception as exc:  # pragma: no cover
        raise ValueError(
            "ENCRYPTION_KEY is not valid URL-safe base64 — cannot derive PAT signing key"
        ) from exc

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_HKDF_INFO,
    )
    return hkdf.derive(key_bytes)


# ── Public helpers ────────────────────────────────────────────────────────────

def hash_token(plaintext: str) -> str:
    """Compute the HMAC-SHA256 hex digest of *plaintext* using the server key.

    This function is the canonical hash function used by SFBL-367 to look up
    incoming bearer tokens in the database.

    Args:
        plaintext: The raw token string (``sfbl_pat_<entropy>``).

    Returns:
        64-character lowercase hex string.
    """
    from app.config import settings  # lazy import to avoid circular deps

    signing_key = _derive_signing_key(settings.encryption_key)
    return hmac.new(signing_key, plaintext.encode(), sha256).hexdigest()


def _hash_token_with_key(plaintext: str, signing_key: bytes) -> str:
    """Internal variant that accepts an already-derived key (avoids re-deriving in a loop)."""
    return hmac.new(signing_key, plaintext.encode(), sha256).hexdigest()


# ── Issuance ──────────────────────────────────────────────────────────────────

async def issue(
    db: AsyncSession,
    user: "app.models.user.User",  # type: ignore[name-defined]
    name: str,
    *,
    expires_at: Optional[datetime] = None,
    scope: Optional[str] = None,
) -> tuple[PersonalAccessToken, str]:
    """Issue a new Personal Access Token for *user*.

    Args:
        db: Async database session. The caller is responsible for committing
            after this function returns.
        user: The owning :class:`~app.models.user.User` instance.
        name: Human-readable label (e.g. ``"CI pipeline"``).
        expires_at: Optional absolute expiry. Pass ``None`` for a non-expiring
            token. Must be timezone-aware if supplied.
        scope: Optional forward-compat scope string (NOT enforced in v1).
            Callers should pass a JSON array string or ``None``.

    Returns:
        A tuple ``(pat_row, plaintext)``.  ``plaintext`` is the full token string
        that must be returned to the user ONCE.  It is not stored and cannot be
        recovered after this call.

    Raises:
        ValueError: If ``expires_at`` is naive (no timezone info).
    """
    if expires_at is not None and expires_at.tzinfo is None:
        raise ValueError("expires_at must be timezone-aware")

    from app.config import settings  # lazy import

    signing_key = _derive_signing_key(settings.encryption_key)

    # Generate the plaintext token
    entropy = secrets.token_urlsafe(_TOKEN_ENTROPY_BYTES)
    plaintext = f"{TOKEN_PREFIX}{entropy}"

    # Compute hash and display fields — do NOT log plaintext
    token_hash = _hash_token_with_key(plaintext, signing_key)
    last4 = plaintext[-4:]

    pat = PersonalAccessToken(
        user_id=user.id,
        name=name,
        token_hash=token_hash,
        prefix=TOKEN_PREFIX,
        last4=last4,
        expires_at=expires_at,
        scope=scope,
        # created_at has a server_default; set explicitly for test determinism
        created_at=datetime.now(timezone.utc),
    )
    db.add(pat)
    await db.flush()  # populate pat.id without committing

    _log.info(
        "PAT issued for user %s (name=%r)",
        user.id,
        name,
        extra={
            "event_name": AuthEvent.PAT_ISSUED,
            "outcome_code": OutcomeCode.SUCCESS,
            "user_id": user.id,
            "pat_id": pat.id,
            # last4 is safe — cannot be used to reconstruct the token
            "pat_last4": last4,
        },
    )

    # Return plaintext ONCE — caller must deliver it to the user immediately
    return pat, plaintext


# ── Revocation ────────────────────────────────────────────────────────────────

async def revoke(
    db: AsyncSession,
    token: PersonalAccessToken,
) -> None:
    """Revoke *token* by setting ``revoked_at`` to now.

    Idempotent: if the token is already revoked, the timestamp is NOT updated
    (first-revocation timestamp is preserved for audit purposes).

    Args:
        db: Async database session. The caller is responsible for committing.
        token: The :class:`PersonalAccessToken` row to revoke.
    """
    if token.revoked_at is not None:
        # Already revoked — preserve original timestamp for audit
        return

    token.revoked_at = datetime.now(timezone.utc)
    db.add(token)
    await db.flush()

    _log.info(
        "PAT revoked (id=%s, user_id=%s, name=%r)",
        token.id,
        token.user_id,
        token.name,
        extra={
            "event_name": AuthEvent.PAT_REVOKED,
            "outcome_code": OutcomeCode.SUCCESS,
            "user_id": token.user_id,
            "pat_id": token.id,
        },
    )
