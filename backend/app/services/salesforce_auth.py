"""
Salesforce OAuth 2.0 JWT Bearer flow authentication service.

Flow (per spec §4.1):
  1. Build a JWT signed with RS256 using the connection's RSA private key.
  2. POST the assertion to /services/oauth2/token.
  3. Receive access_token (and derive expiry — Salesforce omits expires_in).
  4. Cache the token in the DB; transparently refresh before it expires.

Private keys are stored encrypted in the DB using Fernet symmetric encryption.
The Fernet key is taken from the ENCRYPTION_KEY environment variable.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from jose import jwt
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.connection import Connection
from app.utils.encryption import EncryptionError, decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"

# Salesforce enforces a max JWT lifetime of 3 minutes.
_JWT_LIFETIME_SECONDS = 180

# Salesforce access tokens are valid for 2 hours (7 200 s) by default.
# The response does not include expires_in, so we derive it ourselves.
_TOKEN_LIFETIME_SECONDS = 7_200

# Start refreshing this many seconds before the token would actually expire
# so callers are never mid-request when the token dies.
_TOKEN_REFRESH_BUFFER_SECONDS = 300


# ── Exceptions ────────────────────────────────────────────────────────────────


class AuthError(Exception):
    """Raised when a Salesforce authentication step fails."""


# ── Encryption helpers (backward-compat wrappers over shared utility) ─────────

# Keep the old names and error type so existing callers don't break.
def encrypt_private_key(pem: str) -> str:
    """Encrypt a PEM private key; raises AuthError on failure."""
    try:
        return encrypt_secret(pem)
    except EncryptionError as exc:
        raise AuthError(str(exc)) from exc


def decrypt_private_key(encrypted: str) -> str:
    """Decrypt a Fernet-encrypted private key; raises AuthError on failure."""
    try:
        return decrypt_secret(encrypted)
    except EncryptionError as exc:
        raise AuthError(str(exc)) from exc


# ── JWT construction ──────────────────────────────────────────────────────────


def _build_jwt(connection: Connection, private_key_pem: str) -> str:
    """Construct and sign the RS256 JWT assertion for the Bearer flow.

    Claims (per Salesforce JWT Bearer spec):
        iss — Connected App ``client_id``
        sub — Salesforce ``username``
        aud — Login URL (e.g. ``https://login.salesforce.com``)
        exp — ``now + _JWT_LIFETIME_SECONDS``

    Args:
        connection: The :class:`~app.models.connection.Connection` record.
        private_key_pem: Decrypted PEM string of the RSA private key.

    Returns:
        Compact JWT string.
    """
    now = int(datetime.now(tz=timezone.utc).timestamp())
    claims = {
        "iss": connection.client_id,
        "sub": connection.username,
        "aud": connection.login_url,
        "exp": now + _JWT_LIFETIME_SECONDS,
    }
    return jwt.encode(claims, private_key_pem, algorithm="RS256")


# ── Token exchange ────────────────────────────────────────────────────────────


async def _exchange_jwt(
    login_url: str,
    assertion: str,
    client: httpx.AsyncClient,
) -> tuple[str, datetime]:
    """POST the signed JWT to Salesforce and return the token + derived expiry.

    Args:
        login_url: Base login URL (e.g. ``https://login.salesforce.com``).
        assertion: Compact JWT produced by :func:`_build_jwt`.
        client: An active :class:`httpx.AsyncClient`.

    Returns:
        ``(access_token, expiry_utc)`` where *expiry_utc* is a timezone-aware
        datetime set to ``now + _TOKEN_LIFETIME_SECONDS``.

    Raises:
        AuthError: If Salesforce returns a non-200 response or omits
            ``access_token``.
    """
    url = f"{login_url.rstrip('/')}/services/oauth2/token"
    response = await client.post(
        url,
        data={
            "grant_type": _JWT_BEARER_GRANT,
            "assertion": assertion,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    if response.status_code != 200:
        raise AuthError(
            f"Salesforce token exchange failed "
            f"[{response.status_code}]: {response.text}"
        )

    body = response.json()
    access_token: Optional[str] = body.get("access_token")
    if not access_token:
        raise AuthError(f"No access_token in Salesforce response: {body}")

    expiry = datetime.now(tz=timezone.utc) + timedelta(seconds=_TOKEN_LIFETIME_SECONDS)
    return access_token, expiry


# ── Token validity check ──────────────────────────────────────────────────────


def _is_token_valid(connection: Connection) -> bool:
    """Return True when the stored token exists and won't expire within the buffer.

    Args:
        connection: Connection record with cached ``access_token`` /
            ``token_expiry``.
    """
    if not connection.access_token or not connection.token_expiry:
        return False

    expiry: datetime = connection.token_expiry
    if expiry.tzinfo is None:
        # SQLite returns naive datetimes — assume UTC.
        expiry = expiry.replace(tzinfo=timezone.utc)

    return datetime.now(tz=timezone.utc) < expiry - timedelta(
        seconds=_TOKEN_REFRESH_BUFFER_SECONDS
    )


# ── Public API ────────────────────────────────────────────────────────────────


async def get_access_token(
    db: AsyncSession,
    connection: Connection,
    *,
    http_client: Optional[httpx.AsyncClient] = None,
) -> str:
    """Return a valid Salesforce access token, refreshing transparently.

    If the cached token on *connection* is still fresh (>5 min remaining),
    it is returned immediately with no network call.  Otherwise a new JWT
    Bearer exchange is performed, the new token is persisted to the DB and
    cached on the in-memory *connection* object.

    Args:
        db: Active async DB session used to persist the refreshed token.
        connection: The :class:`~app.models.connection.Connection` record.
        http_client: Optional pre-created :class:`httpx.AsyncClient`.  Pass
            this in tests (or when a shared client exists) to avoid creating
            a new one per call.

    Returns:
        A valid Salesforce access token string.

    Raises:
        AuthError: On decryption failures or Salesforce API errors.
    """
    if _is_token_valid(connection):
        return connection.access_token  # type: ignore[return-value]

    logger.info(
        "Acquiring new Salesforce access token for connection %s", connection.id
    )

    try:
        private_key_pem = decrypt_private_key(connection.private_key)
    except EncryptionError as exc:
        raise AuthError(str(exc)) from exc
    assertion = _build_jwt(connection, private_key_pem)

    if http_client is not None:
        access_token, expiry = await _exchange_jwt(
            connection.login_url, assertion, http_client
        )
    else:
        async with httpx.AsyncClient() as client:
            access_token, expiry = await _exchange_jwt(
                connection.login_url, assertion, client
            )

    # Persist to DB so the token survives across requests / worker restarts.
    await db.execute(
        update(Connection)
        .where(Connection.id == connection.id)
        .values(access_token=access_token, token_expiry=expiry)
    )
    await db.commit()

    # Keep the in-memory object consistent to avoid a spurious re-fetch on the
    # same connection reference within the current request lifetime.
    connection.access_token = access_token
    connection.token_expiry = expiry

    return access_token
