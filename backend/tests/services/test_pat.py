"""Unit tests for the PAT issuance service (SFBL-366).

Tests cover:
- hash determinism (same plaintext → same hash)
- distinct tokens → distinct hashes
- plaintext returned once, only hash persisted in the DB
- prefix and last4 correctness
- constant-time compare via hash_token matches token_hash
- revoke sets revoked_at
- revoke is idempotent (preserves first revoked_at timestamp)
- expires_at validation (naive datetime rejected)
- issue emits PAT_ISSUED observability log record
- revoke emits PAT_REVOKED observability log record

These tests are pure unit tests that do NOT hit a live database.
The DB interactions are tested via the conftest fixtures + TestClient for API
tests (SFBL-368). We use an in-memory SQLite session for the few tests that
need flush() to populate pat.id.
"""

from __future__ import annotations

import asyncio
import hmac
import os
from datetime import datetime, timezone
from hashlib import sha256
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

# ── Isolate tests from repo-root .env ─────────────────────────────────────────
os.environ.setdefault("SFBL_DISABLE_ENV_FILE", "1")
os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-for-pat-tests")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.example.com")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "Test-Admin-P4ss!")

from app.services.pat import (  # noqa: E402
    TOKEN_PREFIX,
    _derive_signing_key,
    _hash_token_with_key,
    hash_token,
    issue,
    revoke,
)
from app.models.personal_access_token import PersonalAccessToken  # noqa: E402
from app.observability.events import AuthEvent  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_user(user_id: str = "user-test-1234"):
    """Return a minimal mock User object sufficient for issue()."""
    user = MagicMock()
    user.id = user_id
    return user


def _run(coro):
    """Run a coroutine synchronously (for tests outside an async framework).

    Uses a fresh event loop rather than ``asyncio.get_event_loop()`` — on
    Python 3.12 the latter raises ``RuntimeError: There is no current event
    loop`` when no loop has been set in the thread (which is the case when this
    test runs before any loop-creating test), causing CI-order-dependent flakes.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── hash_token determinism ─────────────────────────────────────────────────────

class TestHashTokenDeterminism:
    """hash_token must produce the same digest for the same input."""

    def test_same_plaintext_same_hash(self):
        plaintext = "sfbl_pat_test_token_abc123"
        h1 = hash_token(plaintext)
        h2 = hash_token(plaintext)
        assert h1 == h2, "hash_token must be deterministic"

    def test_returns_hex_string(self):
        plaintext = "sfbl_pat_test_token_abc123"
        result = hash_token(plaintext)
        # SHA-256 hex digest is always 64 chars
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_distinct_plaintexts_distinct_hashes(self):
        h1 = hash_token("sfbl_pat_aaaa")
        h2 = hash_token("sfbl_pat_bbbb")
        assert h1 != h2, "distinct tokens must produce distinct hashes"

    def test_hash_is_keyed_not_plain_sha256(self):
        """The hash must differ from a plain SHA-256 of the plaintext."""
        plaintext = "sfbl_pat_some_token"
        keyed = hash_token(plaintext)
        plain = sha256(plaintext.encode()).hexdigest()
        assert keyed != plain, "hash_token should use HMAC, not raw SHA-256"


# ── _derive_signing_key ────────────────────────────────────────────────────────

class TestDeriveSigningKey:
    """Key derivation must be stable and isolated from the raw Fernet key."""

    def test_same_key_same_derived_key(self):
        enc_key = Fernet.generate_key().decode()
        k1 = _derive_signing_key(enc_key)
        k2 = _derive_signing_key(enc_key)
        assert k1 == k2

    def test_different_encryption_keys_yield_different_signing_keys(self):
        k1 = _derive_signing_key(Fernet.generate_key().decode())
        k2 = _derive_signing_key(Fernet.generate_key().decode())
        assert k1 != k2

    def test_derived_key_differs_from_raw_fernet_bytes(self):
        import base64
        enc_key_str = Fernet.generate_key().decode()
        raw = base64.urlsafe_b64decode(enc_key_str)
        derived = _derive_signing_key(enc_key_str)
        assert derived != raw, "derived key must be distinct from the raw Fernet key bytes"

    def test_output_is_32_bytes(self):
        k = _derive_signing_key(Fernet.generate_key().decode())
        assert len(k) == 32


# ── issue() ───────────────────────────────────────────────────────────────────

class TestIssue:
    """issue() must return a PAT row and the plaintext token ONCE."""

    def _make_async_db(self):
        """Return an async mock session that records db.add() calls."""
        db = MagicMock()
        # flush() is awaited — return a coroutine that resolves to None
        async def _flush():
            pass
        db.flush = _flush
        return db

    def test_plaintext_has_correct_prefix(self):
        db = self._make_async_db()
        user = _make_user()
        pat, plaintext = _run(issue(db, user, name="test-token"))
        assert plaintext.startswith(TOKEN_PREFIX), (
            f"plaintext must start with {TOKEN_PREFIX!r}, got {plaintext[:20]!r}"
        )

    def test_plaintext_not_stored_in_row(self):
        db = self._make_async_db()
        user = _make_user()
        pat, plaintext = _run(issue(db, user, name="ci"))
        # token_hash must not equal the plaintext
        assert pat.token_hash != plaintext
        # token_hash must be 64 hex chars
        assert len(pat.token_hash) == 64

    def test_token_hash_matches_hash_token(self):
        """The persisted token_hash must equal hash_token(plaintext)."""
        db = self._make_async_db()
        user = _make_user()
        pat, plaintext = _run(issue(db, user, name="ci"))
        expected_hash = hash_token(plaintext)
        assert pat.token_hash == expected_hash

    def test_constant_time_compare_succeeds(self):
        """hmac.compare_digest(stored, computed) must return True for a valid token."""
        db = self._make_async_db()
        user = _make_user()
        pat, plaintext = _run(issue(db, user, name="ci"))
        computed = hash_token(plaintext)
        assert hmac.compare_digest(pat.token_hash, computed)

    def test_constant_time_compare_fails_for_wrong_token(self):
        db = self._make_async_db()
        user = _make_user()
        pat, _ = _run(issue(db, user, name="ci"))
        wrong_hash = hash_token("sfbl_pat_totally_wrong_token")
        assert not hmac.compare_digest(pat.token_hash, wrong_hash)

    def test_last4_is_last_4_chars_of_plaintext(self):
        db = self._make_async_db()
        user = _make_user()
        pat, plaintext = _run(issue(db, user, name="ci"))
        assert pat.last4 == plaintext[-4:]

    def test_prefix_stored_correctly(self):
        db = self._make_async_db()
        user = _make_user()
        pat, _ = _run(issue(db, user, name="ci"))
        assert pat.prefix == TOKEN_PREFIX

    def test_user_id_set(self):
        db = self._make_async_db()
        user = _make_user("user-xyz-789")
        pat, _ = _run(issue(db, user, name="ci"))
        assert pat.user_id == "user-xyz-789"

    def test_name_set(self):
        db = self._make_async_db()
        user = _make_user()
        pat, _ = _run(issue(db, user, name="my ci token"))
        assert pat.name == "my ci token"

    def test_expires_at_set(self):
        db = self._make_async_db()
        user = _make_user()
        exp = datetime(2030, 1, 1, tzinfo=timezone.utc)
        pat, _ = _run(issue(db, user, name="ci", expires_at=exp))
        assert pat.expires_at == exp

    def test_expires_at_none_by_default(self):
        db = self._make_async_db()
        user = _make_user()
        pat, _ = _run(issue(db, user, name="ci"))
        assert pat.expires_at is None

    def test_scope_stored(self):
        db = self._make_async_db()
        user = _make_user()
        pat, _ = _run(issue(db, user, name="scoped", scope='["runs.view"]'))
        assert pat.scope == '["runs.view"]'

    def test_scope_none_by_default(self):
        db = self._make_async_db()
        user = _make_user()
        pat, _ = _run(issue(db, user, name="ci"))
        assert pat.scope is None

    def test_naive_expires_at_raises(self):
        db = self._make_async_db()
        user = _make_user()
        naive_dt = datetime(2030, 1, 1)  # no tzinfo
        with pytest.raises(ValueError, match="timezone-aware"):
            _run(issue(db, user, name="ci", expires_at=naive_dt))

    def test_two_issued_tokens_have_distinct_hashes(self):
        db = self._make_async_db()
        user = _make_user()
        pat1, pt1 = _run(issue(db, user, name="tok1"))
        pat2, pt2 = _run(issue(db, user, name="tok2"))
        assert pt1 != pt2
        assert pat1.token_hash != pat2.token_hash

    def test_db_add_called(self):
        db = self._make_async_db()
        user = _make_user()
        _run(issue(db, user, name="ci"))
        db.add.assert_called()

    def test_emits_pat_issued_log(self, caplog):
        import logging
        db = self._make_async_db()
        user = _make_user("log-test-user")
        with caplog.at_level(logging.INFO, logger="app.services.pat"):
            _run(issue(db, user, name="log test"))
        records = [r for r in caplog.records if r.name == "app.services.pat"]
        assert any(
            getattr(r, "event_name", None) == AuthEvent.PAT_ISSUED
            for r in records
        ), "PAT_ISSUED event must be emitted"

    def test_plaintext_not_in_log_records(self, caplog):
        """Raw token plaintext must NOT appear in any log record."""
        import logging
        db = self._make_async_db()
        user = _make_user()
        with caplog.at_level(logging.DEBUG, logger="app.services.pat"):
            _, plaintext = _run(issue(db, user, name="security-check"))
        for record in caplog.records:
            assert plaintext not in record.getMessage(), (
                f"Token plaintext leaked in log record: {record.getMessage()!r}"
            )


# ── revoke() ──────────────────────────────────────────────────────────────────

class TestRevoke:
    """revoke() must set revoked_at and be idempotent."""

    def _make_pat(self, revoked_at=None):
        pat = MagicMock(spec=PersonalAccessToken)
        pat.id = "pat-test-id"
        pat.user_id = "user-test-id"
        pat.name = "test"
        pat.revoked_at = revoked_at
        return pat

    def _make_db(self):
        db = MagicMock()
        async def _flush():
            pass
        db.flush = _flush
        return db

    def test_revoke_sets_revoked_at(self):
        pat = self._make_pat()
        db = self._make_db()
        _run(revoke(db, pat))
        assert pat.revoked_at is not None
        assert pat.revoked_at.tzinfo is not None  # must be tz-aware

    def test_revoke_calls_db_add(self):
        pat = self._make_pat()
        db = self._make_db()
        _run(revoke(db, pat))
        db.add.assert_called_with(pat)

    def test_revoke_idempotent_preserves_original_timestamp(self):
        original_ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        pat = self._make_pat(revoked_at=original_ts)
        db = self._make_db()
        _run(revoke(db, pat))
        assert pat.revoked_at == original_ts, (
            "revoke() must not overwrite an already-set revoked_at"
        )
        db.add.assert_not_called()

    def test_revoke_emits_pat_revoked_log(self, caplog):
        import logging
        pat = self._make_pat()
        db = self._make_db()
        with caplog.at_level(logging.INFO, logger="app.services.pat"):
            _run(revoke(db, pat))
        records = [r for r in caplog.records if r.name == "app.services.pat"]
        assert any(
            getattr(r, "event_name", None) == AuthEvent.PAT_REVOKED
            for r in records
        ), "PAT_REVOKED event must be emitted"

    def test_revoke_already_revoked_no_log(self, caplog):
        """No PAT_REVOKED event when already revoked (idempotent path)."""
        import logging
        original_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        pat = self._make_pat(revoked_at=original_ts)
        db = self._make_db()
        with caplog.at_level(logging.INFO, logger="app.services.pat"):
            _run(revoke(db, pat))
        records = [r for r in caplog.records if r.name == "app.services.pat"]
        assert not any(
            getattr(r, "event_name", None) == AuthEvent.PAT_REVOKED
            for r in records
        ), "PAT_REVOKED must NOT be emitted for an already-revoked token"
