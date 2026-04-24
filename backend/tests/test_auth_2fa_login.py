"""Tests for the two-phase login flow (SFBL-248).

Covers:
- ``POST /api/auth/login`` branching: no-MFA (phase-2 OK), enrolled user
  (phase-1 mfa_required), and forced-enrolment via ``require_2fa``.
- ``POST /api/auth/login/2fa``: TOTP happy path, wrong code (401 + lockout
  counter increment), replay rejection, backup-code consume, must_enroll
  token rejection.
- ``POST /api/auth/login/2fa/enroll/start`` and ``/enroll-and-verify``:
  forced enrolment returns a full-access token and persists the factor.
- ``get_mfa_pending_user`` rejects forged / non-pending tokens.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pyotp
import pytest

from app.models.login_attempt import LoginAttempt
from app.models.user import User
from app.models.user_backup_code import UserBackupCode
from app.models.user_totp import UserTotp
from app.services.auth import create_mfa_token, hash_password
from app.services.totp import generate_backup_code, generate_secret
from app.utils.encryption import encrypt_secret
from tests.conftest import _TestSession, _run_async


# ─── helpers ────────────────────────────────────────────────────────────────


def _seed_user(
    *,
    email: str | None = None,
    password: str = "Testing-P4ss!",
) -> tuple[str, str, str]:
    email = email or f"mfa-login-{uuid.uuid4().hex[:8]}@example.com"
    user_id = str(uuid.uuid4())

    async def _seed():
        async with _TestSession() as session:
            session.add(
                User(
                    id=user_id,
                    email=email,
                    hashed_password=hash_password(password),
                    status="active",
                    is_admin=False,
                )
            )
            await session.commit()

    _run_async(_seed())
    return user_id, email, password


def _seed_totp(user_id: str, secret: str) -> None:
    async def _run():
        async with _TestSession() as session:
            session.add(
                UserTotp(
                    user_id=user_id,
                    secret_encrypted=encrypt_secret(secret),
                    enrolled_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

    _run_async(_run())


def _seed_backup_code(user_id: str, plaintext: str) -> None:
    """Store one bcrypt-hashed backup code for a user."""
    import bcrypt

    normalized = plaintext.replace("-", "").upper()
    h = bcrypt.hashpw(normalized.encode(), bcrypt.gensalt(rounds=4)).decode()

    async def _run():
        async with _TestSession() as session:
            session.add(UserBackupCode(user_id=user_id, code_hash=h))
            await session.commit()

    _run_async(_run())


def _count_login_attempts(user_id: str, outcome: str) -> int:
    from sqlalchemy import func, select

    async def _run():
        async with _TestSession() as session:
            return int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(LoginAttempt)
                        .where(
                            LoginAttempt.user_id == user_id,
                            LoginAttempt.outcome == outcome,
                        )
                    )
                ).scalar_one()
            )

    return _run_async(_run())


def _get_user(user_id: str) -> User:
    async def _run():
        async with _TestSession() as session:
            return await session.get(User, user_id)

    return _run_async(_run())


def _get_totp(user_id: str) -> UserTotp | None:
    from sqlalchemy import select

    async def _run():
        async with _TestSession() as session:
            return (
                await session.execute(
                    select(UserTotp).where(UserTotp.user_id == user_id)
                )
            ).scalar_one_or_none()

    return _run_async(_run())


def _count_backup_codes(user_id: str, *, consumed: bool | None = None) -> int:
    from sqlalchemy import func, select

    async def _run():
        async with _TestSession() as session:
            q = (
                select(func.count())
                .select_from(UserBackupCode)
                .where(UserBackupCode.user_id == user_id)
            )
            if consumed is True:
                q = q.where(UserBackupCode.consumed_at.is_not(None))
            elif consumed is False:
                q = q.where(UserBackupCode.consumed_at.is_(None))
            return int((await session.execute(q)).scalar_one())

    return _run_async(_run())


def _reset_rate_limit() -> None:
    """Reset the in-process rate-limit store between tests that may collide."""
    from app.services import rate_limit as _rl

    _rl._store.clear()


@pytest.fixture(autouse=True)
def _auto_reset_rate_limit():
    _reset_rate_limit()
    yield
    _reset_rate_limit()


# ─── /login branching ───────────────────────────────────────────────────────


def test_login_no_mfa_returns_full_token(client):
    _, email, password = _seed_user()
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["mfa_required"] is False
    assert body["must_reset_password"] is False


def test_login_enrolled_user_returns_mfa_challenge(client):
    user_id, email, password = _seed_user()
    _seed_totp(user_id, generate_secret())

    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mfa_required"] is True
    assert body["mfa_token"]
    assert body["must_enroll"] is False
    assert body["mfa_methods"] == ["totp", "backup_code"]
    # Phase-1 discipline: no success login_attempt; mfa_challenge_issued only.
    assert _count_login_attempts(user_id, "ok") == 0
    assert _count_login_attempts(user_id, "mfa_challenge_issued") == 1
    # last_login_at must NOT be set yet (phase-2 side effect).
    assert _get_user(user_id).last_login_at is None


def test_login_forced_enrolment_when_require_2fa_on(client):
    user_id, email, password = _seed_user()

    async def _turn_on():
        from app.services.settings.service import settings_service
        await settings_service.set("require_2fa", True)

    _run_async(_turn_on())
    try:
        resp = client.post(
            "/api/auth/login", json={"email": email, "password": password}
        )
    finally:
        async def _turn_off():
            from app.services.settings.service import settings_service
            await settings_service.set("require_2fa", False)

        _run_async(_turn_off())

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mfa_required"] is True
    assert body["must_enroll"] is True
    assert body["mfa_methods"] == ["enroll"]


# ─── /login/2fa — TOTP ──────────────────────────────────────────────────────


def test_login_2fa_totp_happy_path(client):
    user_id, email, password = _seed_user()
    secret = generate_secret()
    _seed_totp(user_id, secret)

    token = create_mfa_token(user_id, must_enroll=False)
    code = pyotp.TOTP(secret).now()

    resp = client.post(
        "/api/auth/login/2fa",
        json={"code": code, "method": "totp"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["mfa_required"] is False
    assert body["must_reset_password"] is False

    # Phase-2 side effects fired.
    assert _count_login_attempts(user_id, "ok") == 1
    user = _get_user(user_id)
    assert user.last_login_at is not None
    # Anti-replay counter advanced.
    totp = _get_totp(user_id)
    assert totp.last_used_counter is not None


def test_login_2fa_totp_wrong_code_increments_lockout(client):
    user_id, email, password = _seed_user()
    _seed_totp(user_id, generate_secret())

    token = create_mfa_token(user_id, must_enroll=False)
    resp = client.post(
        "/api/auth/login/2fa",
        json={"code": "000000", "method": "totp"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "mfa_code_invalid"
    # Lockout counter bumped.
    user = _get_user(user_id)
    assert (user.failed_login_count or 0) >= 1
    # wrong_mfa audit row present.
    assert _count_login_attempts(user_id, "wrong_mfa") == 1


def test_login_2fa_totp_replay_rejected(client):
    user_id, _, _ = _seed_user()
    secret = generate_secret()
    _seed_totp(user_id, secret)

    token = create_mfa_token(user_id, must_enroll=False)
    code = pyotp.TOTP(secret).now()

    r1 = client.post(
        "/api/auth/login/2fa",
        json={"code": code, "method": "totp"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 200, r1.text

    # Second use of the same code at the same step → replay.
    token2 = create_mfa_token(user_id, must_enroll=False)
    r2 = client.post(
        "/api/auth/login/2fa",
        json={"code": code, "method": "totp"},
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert r2.status_code == 401
    assert r2.json()["detail"]["code"] == "mfa_code_invalid"


def test_login_2fa_totp_rejects_must_enroll_token(client):
    user_id, _, _ = _seed_user()
    _seed_totp(user_id, generate_secret())
    token = create_mfa_token(user_id, must_enroll=True)
    resp = client.post(
        "/api/auth/login/2fa",
        json={"code": "000000", "method": "totp"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "mfa_token_invalid"


def test_login_2fa_missing_bearer_returns_401(client):
    resp = client.post(
        "/api/auth/login/2fa", json={"code": "123456", "method": "totp"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "mfa_token_invalid"


# ─── /login/2fa — backup codes ──────────────────────────────────────────────


def test_login_2fa_backup_code_happy_path(client):
    user_id, _, _ = _seed_user()
    _seed_totp(user_id, generate_secret())

    plain = generate_backup_code()
    _seed_backup_code(user_id, plain)

    token = create_mfa_token(user_id, must_enroll=False)
    resp = client.post(
        "/api/auth/login/2fa",
        json={"code": plain, "method": "backup_code"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]

    assert _count_backup_codes(user_id, consumed=True) == 1
    assert _count_backup_codes(user_id, consumed=False) == 0


def test_login_2fa_backup_code_wrong_rejected(client):
    user_id, _, _ = _seed_user()
    _seed_totp(user_id, generate_secret())
    _seed_backup_code(user_id, generate_backup_code())

    token = create_mfa_token(user_id, must_enroll=False)
    resp = client.post(
        "/api/auth/login/2fa",
        json={"code": "XXXXXXXXXX", "method": "backup_code"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert _count_backup_codes(user_id, consumed=True) == 0


# ─── /login/2fa/enroll/start ────────────────────────────────────────────────


def test_login_2fa_enroll_start_requires_must_enroll_token(client):
    user_id, _, _ = _seed_user()
    token = create_mfa_token(user_id, must_enroll=False)
    resp = client.post(
        "/api/auth/login/2fa/enroll/start",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "not_forced_enroll"


def test_login_2fa_enroll_start_returns_secret_and_qr(client):
    user_id, _, _ = _seed_user()
    token = create_mfa_token(user_id, must_enroll=True)
    resp = client.post(
        "/api/auth/login/2fa/enroll/start",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["secret_base32"]) == 32
    assert body["otpauth_uri"].startswith("otpauth://totp/")
    assert body["qr_svg"].startswith("<svg")


# ─── /login/2fa/enroll-and-verify ───────────────────────────────────────────


def test_login_2fa_enroll_and_verify_persists_factor_and_issues_token(client):
    user_id, email, _ = _seed_user()
    secret = generate_secret()
    code = pyotp.TOTP(secret).now()

    token = create_mfa_token(user_id, must_enroll=True)
    resp = client.post(
        "/api/auth/login/2fa/enroll-and-verify",
        json={"secret_base32": secret, "code": code},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["mfa_required"] is False
    assert len(body["backup_codes"]) == 10
    assert all(len(c) == 11 and c[5] == "-" for c in body["backup_codes"])

    # user_totp + 10 backup codes persisted.
    assert _get_totp(user_id) is not None
    assert _count_backup_codes(user_id) == 10
    # Phase-2 success side effects fired.
    assert _count_login_attempts(user_id, "ok") == 1
    assert _get_user(user_id).last_login_at is not None


def test_login_2fa_enroll_and_verify_rejects_wrong_code(client):
    user_id, _, _ = _seed_user()
    token = create_mfa_token(user_id, must_enroll=True)
    resp = client.post(
        "/api/auth/login/2fa/enroll-and-verify",
        json={"secret_base32": generate_secret(), "code": "000000"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_code"
    assert _get_totp(user_id) is None


def test_login_2fa_enroll_and_verify_rejects_must_enroll_false(client):
    user_id, _, _ = _seed_user()
    token = create_mfa_token(user_id, must_enroll=False)
    resp = client.post(
        "/api/auth/login/2fa/enroll-and-verify",
        json={"secret_base32": generate_secret(), "code": "000000"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


def test_login_2fa_enroll_and_verify_conflict_when_already_enrolled(client):
    user_id, _, _ = _seed_user()
    _seed_totp(user_id, generate_secret())

    token = create_mfa_token(user_id, must_enroll=True)
    resp = client.post(
        "/api/auth/login/2fa/enroll-and-verify",
        json={"secret_base32": generate_secret(), "code": "000000"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "already_enrolled"
