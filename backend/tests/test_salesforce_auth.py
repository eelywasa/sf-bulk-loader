"""Tests for app.services.salesforce_auth.

Covers:
  - Fernet encrypt/decrypt round-trip and bad-key failure.
  - JWT construction: claims, algorithm, and signature verifiability.
  - _is_token_valid: fresh, expiring-soon, naive-datetime, and missing cases.
  - _exchange_jwt: success, HTTP error, and missing access_token cases.
  - get_access_token: cache-hit, expired-token refresh with DB persist, and
    propagation of exchange errors.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt as jose_jwt

from app.services.salesforce_auth import (
    AuthError,
    _build_jwt,
    _exchange_jwt,
    _is_token_valid,
    decrypt_private_key,
    encrypt_private_key,
    get_access_token,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def rsa_key_pair():
    """Generate a 2048-bit RSA key pair once per test session."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


@pytest.fixture
def fernet_key() -> str:
    """A fresh Fernet key for each test."""
    return Fernet.generate_key().decode()


@pytest.fixture
def mock_connection(rsa_key_pair, fernet_key) -> MagicMock:
    """A Connection-like object with all auth fields populated."""
    private_pem, _ = rsa_key_pair

    with patch("app.services.salesforce_auth.settings") as mock_settings:
        mock_settings.encryption_key = fernet_key
        encrypted_key = encrypt_private_key(private_pem)

    conn = MagicMock()
    conn.id = "conn-test-123"
    conn.client_id = "3MVG9test_client_id"
    conn.username = "test@example.com"
    conn.login_url = "https://login.salesforce.com"
    conn.private_key = encrypted_key
    conn.access_token = None
    conn.token_expiry = None
    return conn


# ── Fernet encryption tests ───────────────────────────────────────────────────


class TestEncryptDecrypt:
    def test_roundtrip(self, fernet_key):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nFAKEDATA\n-----END RSA PRIVATE KEY-----"
        with patch("app.services.salesforce_auth.settings") as s:
            s.encryption_key = fernet_key
            encrypted = encrypt_private_key(pem)
            recovered = decrypt_private_key(encrypted)
        assert recovered == pem

    def test_encrypted_differs_from_plaintext(self, fernet_key):
        pem = "SOME_KEY_DATA"
        with patch("app.services.salesforce_auth.settings") as s:
            s.encryption_key = fernet_key
            encrypted = encrypt_private_key(pem)
        assert encrypted != pem

    def test_decrypt_wrong_key_raises_auth_error(self, fernet_key):
        pem = "SOME_KEY_DATA"
        wrong_key = Fernet.generate_key().decode()
        with patch("app.services.salesforce_auth.settings") as s:
            s.encryption_key = fernet_key
            encrypted = encrypt_private_key(pem)

        with patch("app.services.salesforce_auth.settings") as s:
            s.encryption_key = wrong_key
            with pytest.raises(AuthError, match="decrypt private key"):
                decrypt_private_key(encrypted)

    def test_missing_encryption_key_raises(self):
        with patch("app.services.salesforce_auth.settings") as s:
            s.encryption_key = ""
            with pytest.raises(AuthError, match="ENCRYPTION_KEY"):
                encrypt_private_key("anything")


# ── JWT construction tests ────────────────────────────────────────────────────


class TestBuildJwt:
    def test_claims_present(self, mock_connection, rsa_key_pair, fernet_key):
        private_pem, public_pem = rsa_key_pair
        token = _build_jwt(mock_connection, private_pem)

        # Decode without verification to inspect claims
        unverified = jose_jwt.get_unverified_claims(token)
        assert unverified["iss"] == mock_connection.client_id
        assert unverified["sub"] == mock_connection.username
        assert unverified["aud"] == mock_connection.login_url
        assert "exp" in unverified

    def test_expiry_roughly_three_minutes(self, mock_connection, rsa_key_pair):
        private_pem, _ = rsa_key_pair
        before = int(datetime.now(tz=timezone.utc).timestamp())
        token = _build_jwt(mock_connection, private_pem)
        after = int(datetime.now(tz=timezone.utc).timestamp())

        claims = jose_jwt.get_unverified_claims(token)
        exp = claims["exp"]
        # exp should be within [before+180, after+180] (3-minute window)
        assert before + 180 <= exp <= after + 180

    def test_signature_verifiable_with_public_key(self, mock_connection, rsa_key_pair):
        private_pem, public_pem = rsa_key_pair
        token = _build_jwt(mock_connection, private_pem)

        # Verify using the matching public key — raises if invalid
        claims = jose_jwt.decode(
            token,
            public_pem,
            algorithms=["RS256"],
            audience=mock_connection.login_url,
        )
        assert claims["sub"] == mock_connection.username

    def test_wrong_key_fails_verification(self, mock_connection, rsa_key_pair):
        private_pem, _ = rsa_key_pair
        token = _build_jwt(mock_connection, private_pem)

        # Generate an unrelated public key
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        other_public_pem = other_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

        with pytest.raises(Exception):
            jose_jwt.decode(
                token,
                other_public_pem,
                algorithms=["RS256"],
                audience=mock_connection.login_url,
            )


# ── _is_token_valid tests ─────────────────────────────────────────────────────


class TestIsTokenValid:
    def _conn(self, access_token=None, token_expiry=None):
        c = MagicMock()
        c.access_token = access_token
        c.token_expiry = token_expiry
        return c

    def test_no_token_returns_false(self):
        assert _is_token_valid(self._conn()) is False

    def test_no_expiry_returns_false(self):
        assert _is_token_valid(self._conn(access_token="tok")) is False

    def test_fresh_token_returns_true(self):
        expiry = datetime.now(tz=timezone.utc) + timedelta(hours=1)
        assert _is_token_valid(self._conn("tok", expiry)) is True

    def test_expiring_soon_returns_false(self):
        # Within the 300-second buffer
        expiry = datetime.now(tz=timezone.utc) + timedelta(seconds=299)
        assert _is_token_valid(self._conn("tok", expiry)) is False

    def test_expired_returns_false(self):
        expiry = datetime.now(tz=timezone.utc) - timedelta(minutes=5)
        assert _is_token_valid(self._conn("tok", expiry)) is False

    def test_naive_datetime_treated_as_utc(self):
        # SQLite returns naive datetimes; service should handle them
        expiry = datetime.utcnow() + timedelta(hours=1)
        assert expiry.tzinfo is None
        assert _is_token_valid(self._conn("tok", expiry)) is True


# ── _exchange_jwt tests ───────────────────────────────────────────────────────


class TestExchangeJwt:
    @pytest.mark.asyncio
    async def test_success_returns_token_and_expiry(self):
        payload = {"access_token": "sf_access_token_abc", "instance_url": "https://myorg.my.salesforce.com"}
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = payload

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        before = datetime.now(tz=timezone.utc)
        token, expiry = await _exchange_jwt(
            "https://login.salesforce.com", "test.jwt.assertion", mock_client
        )
        after = datetime.now(tz=timezone.utc)

        assert token == "sf_access_token_abc"
        assert before + timedelta(seconds=7000) < expiry < after + timedelta(seconds=7300)

    @pytest.mark.asyncio
    async def test_non_200_raises_auth_error(self):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "invalid_grant"

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(AuthError, match="400"):
            await _exchange_jwt("https://login.salesforce.com", "jwt", mock_client)

    @pytest.mark.asyncio
    async def test_missing_access_token_raises_auth_error(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"instance_url": "https://org.salesforce.com"}

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(AuthError, match="No access_token"):
            await _exchange_jwt("https://login.salesforce.com", "jwt", mock_client)

    @pytest.mark.asyncio
    async def test_posts_to_correct_url(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "tok"}

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        await _exchange_jwt("https://test.salesforce.com/", "jwt", mock_client)

        call_kwargs = mock_client.post.call_args
        assert call_kwargs[0][0] == "https://test.salesforce.com/services/oauth2/token"

    @pytest.mark.asyncio
    async def test_trailing_slash_normalised(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "tok"}

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        await _exchange_jwt("https://login.salesforce.com///", "jwt", mock_client)

        url = mock_client.post.call_args[0][0]
        assert "//" not in url.replace("https://", "")


# ── get_access_token integration tests ───────────────────────────────────────


class TestGetAccessToken:
    def _make_db(self):
        """Return a minimal async DB mock."""
        db = AsyncMock()
        db.execute = AsyncMock()
        db.commit = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_returns_cached_token_when_valid(self, mock_connection):
        mock_connection.access_token = "cached_token"
        mock_connection.token_expiry = datetime.now(tz=timezone.utc) + timedelta(hours=1)

        db = self._make_db()
        result = await get_access_token(db, mock_connection)

        assert result == "cached_token"
        db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_refreshes_expired_token(self, mock_connection, fernet_key):
        mock_connection.access_token = "old_token"
        mock_connection.token_expiry = datetime.now(tz=timezone.utc) - timedelta(hours=1)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "new_token"}
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        db = self._make_db()

        with patch("app.services.salesforce_auth.settings") as s:
            s.encryption_key = fernet_key
            result = await get_access_token(db, mock_connection, http_client=mock_client)

        assert result == "new_token"
        assert mock_connection.access_token == "new_token"
        db.execute.assert_called_once()
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_refreshes_when_no_token(self, mock_connection, fernet_key):
        mock_connection.access_token = None
        mock_connection.token_expiry = None

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "fresh_token"}
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        db = self._make_db()

        with patch("app.services.salesforce_auth.settings") as s:
            s.encryption_key = fernet_key
            result = await get_access_token(db, mock_connection, http_client=mock_client)

        assert result == "fresh_token"

    @pytest.mark.asyncio
    async def test_updates_connection_in_memory(self, mock_connection, fernet_key):
        mock_connection.access_token = None
        mock_connection.token_expiry = None

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "updated_token"}
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        db = self._make_db()

        with patch("app.services.salesforce_auth.settings") as s:
            s.encryption_key = fernet_key
            await get_access_token(db, mock_connection, http_client=mock_client)

        assert mock_connection.access_token == "updated_token"
        assert mock_connection.token_expiry is not None

    @pytest.mark.asyncio
    async def test_propagates_exchange_error(self, mock_connection, fernet_key):
        mock_connection.access_token = None
        mock_connection.token_expiry = None

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "invalid_grant: user hasn't approved"
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        db = self._make_db()

        with patch("app.services.salesforce_auth.settings") as s:
            s.encryption_key = fernet_key
            with pytest.raises(AuthError, match="401"):
                await get_access_token(db, mock_connection, http_client=mock_client)

        db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_uses_internal_client_when_none_provided(
        self, mock_connection, fernet_key
    ):
        """When no client is passed, get_access_token opens its own httpx client."""
        mock_connection.access_token = None
        mock_connection.token_expiry = None

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "auto_client_token"}

        mock_client_instance = AsyncMock(spec=httpx.AsyncClient)
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=None)

        db = self._make_db()

        with patch("app.services.salesforce_auth.settings") as s:
            s.encryption_key = fernet_key
            with patch(
                "app.services.salesforce_auth.httpx.AsyncClient",
                return_value=mock_client_instance,
            ):
                result = await get_access_token(db, mock_connection)

        assert result == "auto_client_token"
