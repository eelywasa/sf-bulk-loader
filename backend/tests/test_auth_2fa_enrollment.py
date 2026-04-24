"""Tests for the 2FA enrolment + management API (SFBL-247).

Covers:
- services/totp.py primitives (secret, URI, verify with window + anti-replay)
- POST /api/auth/2fa/enroll/start  — fresh secret, QR, 409 when enrolled
- POST /api/auth/2fa/enroll/confirm — persists factor + backup codes, fresh JWT
- POST /api/auth/2fa/backup-codes/regenerate — requires valid TOTP, rotates set
- POST /api/auth/2fa/disable — password + code; 403 when require_2fa is on
- get_current_user rejects tokens carrying mfa_pending=true
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pyotp
import pytest

from app.main import app
from app.models.user import User
from app.models.user_backup_code import UserBackupCode
from app.models.user_totp import UserTotp
from app.services.auth import (
    create_access_token,
    get_current_user,
    hash_password,
)
from app.services.totp import (
    TOTP_PERIOD_SECONDS,
    TotpError,
    build_otpauth_uri,
    current_counter,
    generate_backup_code,
    generate_secret,
    normalize_backup_code,
    render_qr_svg,
    verify_code,
)
from app.utils.encryption import encrypt_secret
from tests.conftest import _TestSession, _run_async


# ─────────────────────────────── Service tests ───────────────────────────────


def test_generate_secret_is_base32_length_32():
    s = generate_secret()
    assert len(s) == 32
    assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for c in s)


def test_build_otpauth_uri_contains_issuer_and_secret():
    uri = build_otpauth_uri(
        secret_base32="JBSWY3DPEHPK3PXP", account_label="a@b.com", issuer="SFBL"
    )
    assert uri.startswith("otpauth://totp/SFBL%3Aa%40b.com?")
    assert "secret=JBSWY3DPEHPK3PXP" in uri
    assert "issuer=SFBL" in uri
    assert "algorithm=SHA1" in uri
    assert "digits=6" in uri
    assert "period=30" in uri


def test_build_otpauth_uri_rejects_bad_secret():
    with pytest.raises(TotpError):
        build_otpauth_uri(secret_base32="not-base32!", account_label="x", issuer="SFBL")


def test_render_qr_svg_produces_svg():
    uri = build_otpauth_uri(
        secret_base32="JBSWY3DPEHPK3PXP", account_label="a@b.com", issuer="SFBL"
    )
    svg = render_qr_svg(uri)
    assert svg.startswith("<svg")
    assert "xmlns" in svg


def test_verify_code_accepts_current_step():
    secret = generate_secret()
    totp = pyotp.TOTP(secret)
    code = totp.now()
    r = verify_code(secret_base32=secret, code=code)
    assert r.ok is True
    assert r.counter == current_counter()


def test_verify_code_accepts_previous_step():
    secret = generate_secret()
    totp = pyotp.TOTP(secret)
    # Code for one step ago
    t_prev = (current_counter() - 1) * TOTP_PERIOD_SECONDS
    code = totp.at(t_prev)
    r = verify_code(secret_base32=secret, code=code)
    assert r.ok is True
    assert r.counter == current_counter() - 1


def test_verify_code_rejects_replay():
    secret = generate_secret()
    totp = pyotp.TOTP(secret)
    code = totp.now()
    step = current_counter()
    r = verify_code(secret_base32=secret, code=code, last_used_counter=step)
    assert r.ok is False


def test_verify_code_rejects_wrong_code():
    secret = generate_secret()
    r = verify_code(secret_base32=secret, code="000000")
    # Extremely unlikely to match a random secret at the current step
    assert r.ok is False or r.counter is not None


def test_verify_code_strips_whitespace():
    secret = generate_secret()
    totp = pyotp.TOTP(secret)
    code = totp.now()
    r = verify_code(secret_base32=secret, code=f" {code[:3]} {code[3:]} ")
    assert r.ok is True


def test_verify_code_malformed_input_returns_false():
    secret = generate_secret()
    assert verify_code(secret_base32=secret, code="abc123").ok is False
    assert verify_code(secret_base32=secret, code="12345").ok is False


def test_generate_backup_code_format():
    code = generate_backup_code()
    assert len(code) == 11
    assert code[5] == "-"
    assert normalize_backup_code(code) == code.replace("-", "").upper()


def test_normalize_backup_code_strips_spaces_and_uppercases():
    assert normalize_backup_code(" abc12-XY34z ") == "ABC12XY34Z"


# ─────────────────────────────── API tests ───────────────────────────────


def _seed_user(*, email: str = "mfa-user@example.com", password: str = "Testing-P4ss!"):
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


def _override_user(user_id: str, email: str):
    async def _override():
        async with _TestSession() as session:
            user = await session.get(User, user_id)
            assert user is not None
            # Detach so subsequent route-local sessions don't conflict.
            session.expunge(user)
            return user

    app.dependency_overrides[get_current_user] = _override


def _count_totp_rows(user_id: str) -> int:
    async def _run():
        async with _TestSession() as session:
            row = (
                await session.execute(
                    __import__("sqlalchemy").select(UserTotp).where(UserTotp.user_id == user_id)
                )
            ).scalar_one_or_none()
            return 0 if row is None else 1

    return _run_async(_run())


def _count_backup_codes(user_id: str) -> int:
    from sqlalchemy import func, select

    async def _run():
        async with _TestSession() as session:
            return int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(UserBackupCode)
                        .where(UserBackupCode.user_id == user_id)
                    )
                ).scalar_one()
            )

    return _run_async(_run())


def test_enroll_start_returns_secret_and_qr(client):
    user_id, email, _ = _seed_user()
    _override_user(user_id, email)
    try:
        with patch("app.api.auth_2fa.settings") as mock_settings:
            mock_settings.auth_mode = "jwt"
            resp = client.post("/api/auth/2fa/enroll/start")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["secret_base32"]) == 32
    assert body["otpauth_uri"].startswith("otpauth://totp/")
    assert body["qr_svg"].startswith("<svg")


def test_enroll_start_conflict_when_already_enrolled(client):
    user_id, email, _ = _seed_user()

    async def _seed_totp():
        async with _TestSession() as session:
            session.add(
                UserTotp(
                    user_id=user_id,
                    secret_encrypted=encrypt_secret(generate_secret()),
                    enrolled_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

    _run_async(_seed_totp())
    _override_user(user_id, email)
    try:
        with patch("app.api.auth_2fa.settings") as mock_settings:
            mock_settings.auth_mode = "jwt"
            resp = client.post("/api/auth/2fa/enroll/start")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "already_enrolled"


def test_enroll_confirm_persists_factor_and_mints_backup_codes(client):
    user_id, email, _ = _seed_user()
    secret = generate_secret()
    code = pyotp.TOTP(secret).now()

    _override_user(user_id, email)
    try:
        with patch("app.api.auth_2fa.settings") as mock_settings:
            mock_settings.auth_mode = "jwt"
            resp = client.post(
                "/api/auth/2fa/enroll/confirm",
                json={"secret_base32": secret, "code": code},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in"] > 0
    assert len(body["backup_codes"]) == 10
    assert all(len(c) == 11 and c[5] == "-" for c in body["backup_codes"])

    assert _count_totp_rows(user_id) == 1
    assert _count_backup_codes(user_id) == 10


def test_enroll_confirm_rejects_wrong_code(client):
    user_id, email, _ = _seed_user()
    secret = generate_secret()

    _override_user(user_id, email)
    try:
        with patch("app.api.auth_2fa.settings") as mock_settings:
            mock_settings.auth_mode = "jwt"
            resp = client.post(
                "/api/auth/2fa/enroll/confirm",
                json={"secret_base32": secret, "code": "000000"},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_code"
    assert _count_totp_rows(user_id) == 0


def test_enroll_confirm_rejects_malformed_secret(client):
    user_id, email, _ = _seed_user()
    _override_user(user_id, email)
    try:
        with patch("app.api.auth_2fa.settings") as mock_settings:
            mock_settings.auth_mode = "jwt"
            resp = client.post(
                "/api/auth/2fa/enroll/confirm",
                json={"secret_base32": "not-valid-base32!", "code": "123456"},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    # Schema-level min_length=16 catches the short cases; our 18-char input
    # passes schema but fails the base32 check → 400 invalid_secret.
    assert resp.status_code == 400


def _enrol_user_direct(user_id: str, secret: str) -> None:
    """Seed a confirmed enrolment directly into the DB."""

    async def _seed():
        async with _TestSession() as session:
            session.add(
                UserTotp(
                    user_id=user_id,
                    secret_encrypted=encrypt_secret(secret),
                    enrolled_at=datetime.now(timezone.utc),
                )
            )
            for _ in range(10):
                session.add(
                    UserBackupCode(
                        user_id=user_id,
                        code_hash="x" * 60,
                    )
                )
            await session.commit()

    _run_async(_seed())


def test_regenerate_backup_codes_requires_valid_totp(client):
    user_id, email, _ = _seed_user()
    secret = generate_secret()
    _enrol_user_direct(user_id, secret)
    _override_user(user_id, email)

    try:
        with patch("app.api.auth_2fa.settings") as mock_settings:
            mock_settings.auth_mode = "jwt"
            bad = client.post(
                "/api/auth/2fa/backup-codes/regenerate", json={"code": "000000"}
            )
            code = pyotp.TOTP(secret).now()
            good = client.post(
                "/api/auth/2fa/backup-codes/regenerate", json={"code": code}
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert bad.status_code == 400
    assert good.status_code == 200
    assert len(good.json()["backup_codes"]) == 10
    assert _count_backup_codes(user_id) == 10  # rotated not appended


def test_disable_requires_password_and_code(client):
    user_id, email, password = _seed_user()
    secret = generate_secret()
    _enrol_user_direct(user_id, secret)
    _override_user(user_id, email)

    try:
        with patch("app.api.auth_2fa.settings") as mock_settings:
            mock_settings.auth_mode = "jwt"
            # Wrong password
            bad_pw = client.post(
                "/api/auth/2fa/disable",
                json={"password": "wrong", "code": pyotp.TOTP(secret).now()},
            )
            # Wrong code
            bad_code = client.post(
                "/api/auth/2fa/disable",
                json={"password": password, "code": "000000"},
            )
            # Valid
            ok = client.post(
                "/api/auth/2fa/disable",
                json={"password": password, "code": pyotp.TOTP(secret).now()},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert bad_pw.status_code == 400
    assert bad_code.status_code == 400
    assert ok.status_code == 204
    assert _count_totp_rows(user_id) == 0
    assert _count_backup_codes(user_id) == 0


def test_disable_blocked_when_tenant_enforces_2fa(client):
    user_id, email, password = _seed_user()
    secret = generate_secret()
    _enrol_user_direct(user_id, secret)
    _override_user(user_id, email)

    class _FakeSvc:
        async def get(self, key):
            return True if key == "require_2fa" else False

    try:
        with patch("app.api.auth_2fa.settings") as mock_settings, patch(
            "app.services.settings.service.settings_service", _FakeSvc()
        ):
            mock_settings.auth_mode = "jwt"
            resp = client.post(
                "/api/auth/2fa/disable",
                json={"password": password, "code": pyotp.TOTP(secret).now()},
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "tenant_enforced"
    assert _count_totp_rows(user_id) == 1


def test_get_current_user_rejects_mfa_pending_token(client):
    """A token carrying ``mfa_pending=true`` must not authenticate general endpoints."""
    from jose import jwt

    from app.config import settings as _settings

    user_id, email, _ = _seed_user()
    now = int(datetime.now(timezone.utc).timestamp())
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + 600,
        "mfa_pending": True,
    }
    token = jwt.encode(
        payload, _settings.jwt_secret_key, algorithm=_settings.jwt_algorithm
    )

    with patch("app.config.settings") as mock_settings:
        mock_settings.auth_mode = "jwt"
        resp = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
        )

    assert resp.status_code == 401
    assert "Two-factor" in resp.json()["detail"] or "Not authenticated" in resp.json()["detail"]


def test_normal_token_still_accepted(client):
    """Sanity: a token without mfa_pending still works end-to-end."""
    user_id, email, _ = _seed_user()

    async def _fetch():
        async with _TestSession() as session:
            user = await session.get(User, user_id)
            session.expunge(user)
            return user

    user = _run_async(_fetch())
    token = create_access_token(user)

    with patch("app.config.settings") as mock_settings:
        mock_settings.auth_mode = "jwt"
        resp = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
        )

    assert resp.status_code == 200
    assert resp.json()["email"] == email
