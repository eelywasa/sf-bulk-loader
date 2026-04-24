"""Pydantic schemas for the 2FA enrolment + management API (SFBL-247).

See ``docs/specs/2fa-totp.md`` §2 for the wire contract. The enrolment flow
is stateless (D11): the server returns the secret on ``/enroll/start`` and
only persists it when the caller confirms a working code via
``/enroll/confirm``.
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class EnrollStartResponse(BaseModel):
    """Response body of ``POST /api/auth/2fa/enroll/start``.

    The caller holds ``secret_base32`` in memory until they post it back to
    ``/enroll/confirm`` along with a working code. The QR SVG is already
    rendered so the frontend can drop it straight into an ``<img>`` tag.
    """

    secret_base32: str
    otpauth_uri: str
    qr_svg: str


class EnrollConfirmRequest(BaseModel):
    secret_base32: str = Field(..., min_length=16, max_length=64)
    code: str = Field(..., min_length=6, max_length=10)


class EnrollConfirmResponse(BaseModel):
    """Response of ``POST /api/auth/2fa/enroll/confirm``.

    Issuing a fresh token is load-bearing: the confirmation bumps
    ``User.password_changed_at`` so the caller's prior JWT is invalidated by
    the watermark check in ``get_current_user``.
    """

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    backup_codes: List[str]


class RegenerateBackupCodesRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=10)


class BackupCodesResponse(BaseModel):
    backup_codes: List[str]


class DisableRequest(BaseModel):
    password: str
    code: str = Field(..., min_length=6, max_length=10)
