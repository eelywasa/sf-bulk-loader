"""Tests for the in-memory sliding-window rate limiter (SFBL-145)."""

import asyncio
import time
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import app.services.rate_limit as rl_module
from app.services.rate_limit import (
    check_and_record,
    hashed_email_key,
    ip_key,
    rate_limit,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _run(coro):
    """Run a coroutine in a temporary event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _fresh_store():
    """Replace the module-level store and lock with fresh instances."""
    rl_module._store.clear()


# ── check_and_record ──────────────────────────────────────────────────────────


class TestCheckAndRecord:
    def setup_method(self):
        _fresh_store()

    def test_within_limit_returns_true(self):
        async def _run_test():
            result = await check_and_record("test:key", limit=3, window_seconds=60)
            assert result is True

        _run(_run_test())

    def test_multiple_within_limit(self):
        async def _run_test():
            for _ in range(5):
                result = await check_and_record("test:multi", limit=5, window_seconds=60)
                assert result is True

        _run(_run_test())

    def test_over_limit_returns_false(self):
        async def _run_test():
            for _ in range(3):
                await check_and_record("test:over", limit=3, window_seconds=60)
            # 4th call should be denied
            result = await check_and_record("test:over", limit=3, window_seconds=60)
            assert result is False

        _run(_run_test())

    def test_over_limit_does_not_record(self):
        """When the limit is exceeded the hit is NOT recorded."""
        async def _run_test():
            key = "test:no-record"
            for _ in range(3):
                await check_and_record(key, limit=3, window_seconds=60)
            # Exceed limit — not recorded
            await check_and_record(key, limit=3, window_seconds=60)
            # Count of recorded timestamps should still be 3
            assert len(rl_module._store[key]) == 3

        _run(_run_test())

    def test_key_isolation(self):
        """Hits to one key do not affect counts on another key."""
        async def _run_test():
            for _ in range(3):
                await check_and_record("key:A", limit=3, window_seconds=60)
            # key:A is now at limit — key:B should still be allowed
            result_b = await check_and_record("key:B", limit=3, window_seconds=60)
            assert result_b is True

            result_a = await check_and_record("key:A", limit=3, window_seconds=60)
            assert result_a is False

        _run(_run_test())

    def test_window_rollover_resets_counter(self):
        """Hits older than window_seconds are reaped and no longer count."""
        async def _run_test():
            key = "test:rollover"
            # Inject stale timestamps directly into the store
            stale_time = time.monotonic() - 3700  # > 1 hour ago
            rl_module._store[key] = deque([stale_time, stale_time, stale_time])

            # Despite 3 pre-seeded hits, all are outside the 1-hour window
            result = await check_and_record(key, limit=3, window_seconds=3600)
            assert result is True

        _run(_run_test())

    def test_partial_window_rollover(self):
        """Only stale entries are reaped; fresh ones remain."""
        async def _run_test():
            key = "test:partial"
            now = time.monotonic()
            stale = now - 120  # 2 minutes ago — stale for a 60-second window
            fresh = now - 10   # 10 seconds ago — within window

            rl_module._store[key] = deque([stale, fresh])
            # Only 1 fresh hit remains after reaping; limit is 2 → allowed
            result = await check_and_record(key, limit=2, window_seconds=60)
            assert result is True
            # Now 2 fresh hits → exactly at limit; next should be denied
            result2 = await check_and_record(key, limit=2, window_seconds=60)
            assert result2 is False

        _run(_run_test())

    def test_limit_1_allows_then_denies(self):
        async def _run_test():
            key = "test:limit1"
            r1 = await check_and_record(key, limit=1, window_seconds=60)
            assert r1 is True
            r2 = await check_and_record(key, limit=1, window_seconds=60)
            assert r2 is False

        _run(_run_test())


# ── rate_limit dependency factory ─────────────────────────────────────────────


class TestRateLimitDependency:
    def setup_method(self):
        _fresh_store()

    def _make_request(self, host: str = "1.2.3.4") -> MagicMock:
        request = MagicMock()
        request.client = MagicMock()
        request.client.host = host
        return request

    def test_under_limit_does_not_raise(self):
        dep = rate_limit(ip_key, limit=5, window_seconds=3600)
        request = self._make_request()

        async def _run_test():
            await dep(request)  # should not raise

        _run(_run_test())

    def test_over_limit_raises_429(self):
        dep = rate_limit(ip_key, limit=2, window_seconds=3600)
        request = self._make_request(host="9.9.9.9")

        async def _run_test():
            await dep(request)
            await dep(request)
            with pytest.raises(HTTPException) as exc_info:
                await dep(request)
            assert exc_info.value.status_code == 429
            assert "Too many requests" in exc_info.value.detail

        _run(_run_test())

    def test_different_ips_isolated(self):
        dep = rate_limit(ip_key, limit=1, window_seconds=3600)

        async def _run_test():
            req_a = self._make_request(host="10.0.0.1")
            req_b = self._make_request(host="10.0.0.2")

            await dep(req_a)  # 10.0.0.1 at limit
            await dep(req_b)  # 10.0.0.2 still allowed

            with pytest.raises(HTTPException):
                await dep(req_a)  # 10.0.0.1 exceeded

        _run(_run_test())


# ── ip_key helper ─────────────────────────────────────────────────────────────


class TestIpKey:
    def test_returns_ip_key_with_prefix(self):
        request = MagicMock()
        request.client = MagicMock()
        request.client.host = "192.168.1.1"
        assert ip_key(request) == "rl:ip:192.168.1.1"

    def test_falls_back_when_client_is_none(self):
        request = MagicMock()
        request.client = None
        key = ip_key(request)
        assert key == "rl:ip:unknown"

    def test_ipv6_address(self):
        request = MagicMock()
        request.client = MagicMock()
        request.client.host = "2001:db8::1"
        assert ip_key(request) == "rl:ip:2001:db8::1"


# ── hashed_email_key helper ───────────────────────────────────────────────────


class TestHashedEmailKey:
    def test_returns_hash_key_with_prefix(self):
        key = hashed_email_key("user@example.com")
        assert key.startswith("rl:email:")
        # SHA-256 hex is 64 chars
        assert len(key) == len("rl:email:") + 64

    def test_case_insensitive(self):
        """Email addresses should be normalised to lowercase before hashing."""
        key_lower = hashed_email_key("user@example.com")
        key_upper = hashed_email_key("USER@EXAMPLE.COM")
        assert key_lower == key_upper

    def test_different_emails_produce_different_keys(self):
        assert hashed_email_key("alice@example.com") != hashed_email_key("bob@example.com")

    def test_raw_email_not_in_key(self):
        """The raw address must not appear in the bucket key."""
        key = hashed_email_key("secret@domain.com")
        assert "secret@domain.com" not in key
        assert "secret" not in key
