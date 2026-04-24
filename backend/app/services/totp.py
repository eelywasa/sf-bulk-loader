"""TOTP primitives for SFBL 2FA (SFBL-244 / SFBL-247).

Thin wrapper around ``pyotp`` that implements the parameters locked in the
spec (SHA1 / 6 digits / 30s period / ±1 step window — §0 D10) plus
anti-replay accounting via a caller-supplied ``last_used_counter`` (§10.1).

The functions here are pure — they do not touch the database. Persistence
(encryption of the secret, ``UserTotp.last_used_counter`` updates) is the
caller's responsibility.
"""

from __future__ import annotations

import re
import secrets as _secrets
import time
import urllib.parse
from dataclasses import dataclass
from typing import Optional

import pyotp
import segno

# RFC 6238 parameters (§0 D10 — locked).
TOTP_ALGORITHM = "SHA1"
TOTP_DIGITS = 6
TOTP_PERIOD_SECONDS = 30

# ±1 step either side of "now" — the standard tolerance for clock skew.
_VERIFY_WINDOW = 1

# Accept only canonical base32 secrets. pyotp generates 16 / 32-char secrets;
# we permit the full RFC 4648 base32 alphabet and lengths in [16, 64] to leave
# room for manually-issued longer secrets without opening the door to garbage.
_BASE32_RE = re.compile(r"^[A-Z2-7]{16,64}$")

# ``_BACKUP_CODE_BYTES`` is chosen so ``secrets.token_urlsafe`` yields ~10
# printable characters (pre-grouping). 7 bytes → base64(no padding) of
# ~10 chars, which displays as ``xxxxx-xxxxx`` after grouping.
_BACKUP_CODE_BYTES = 7


class TotpError(ValueError):
    """Raised for malformed secrets / codes so callers can map to 400."""


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of :func:`verify_code`.

    ``counter`` is the absolute TOTP counter (unix_time // period) of the step
    at which the code matched. Callers persist it as ``last_used_counter`` so
    future replays of the same code are rejected.
    """

    ok: bool
    counter: Optional[int] = None


def generate_secret() -> str:
    """Return a fresh RFC 4648 base32 TOTP secret suitable for pyotp.

    Uses ``pyotp.random_base32()`` which is backed by :mod:`secrets` for
    cryptographic randomness.
    """
    return pyotp.random_base32()


def build_otpauth_uri(*, secret_base32: str, account_label: str, issuer: str) -> str:
    """Build an ``otpauth://totp/...`` provisioning URI.

    The ``issuer`` appears twice in a conforming URI (as the label prefix and
    as a query parameter) so legacy authenticator apps can still recover it.
    Both ``account_label`` and ``issuer`` are URL-encoded defensively since
    they may contain characters like ``@`` or spaces.
    """
    if not _BASE32_RE.match(secret_base32):
        raise TotpError("secret_base32 is not a valid base32 string")
    label = urllib.parse.quote(f"{issuer}:{account_label}", safe="")
    params = urllib.parse.urlencode(
        {
            "secret": secret_base32,
            "issuer": issuer,
            "algorithm": TOTP_ALGORITHM,
            "digits": TOTP_DIGITS,
            "period": TOTP_PERIOD_SECONDS,
        }
    )
    return f"otpauth://totp/{label}?{params}"


def render_qr_svg(otpauth_uri: str) -> str:
    """Render ``otpauth_uri`` as an inline SVG string using segno.

    The SVG is scale=4 (large enough for phone cameras) with a transparent
    background so it renders cleanly in both light and dark themes.
    """
    import io

    qr = segno.make(otpauth_uri, error="M")
    buffer = io.BytesIO()
    qr.save(buffer, kind="svg", scale=4, border=2, xmldecl=False, svgns=True)
    return buffer.getvalue().decode("utf-8")


def current_counter(*, at: Optional[float] = None) -> int:
    """Return the TOTP counter (unix seconds // period) for ``at`` (default: now)."""
    t = at if at is not None else time.time()
    return int(t // TOTP_PERIOD_SECONDS)


def verify_code(
    *,
    secret_base32: str,
    code: str,
    last_used_counter: Optional[int] = None,
    at: Optional[float] = None,
) -> VerifyResult:
    """Verify a user-supplied TOTP code against ``secret_base32``.

    Args:
        secret_base32: The base32 shared secret.
        code: The 6-digit code as entered by the user. Whitespace is stripped.
        last_used_counter: The ``UserTotp.last_used_counter`` of the most
            recent successful verification, or ``None`` if none. A match at or
            below this counter is treated as a replay and rejected.
        at: Override for the "current time" (seconds since epoch). Used in tests.

    Returns:
        :class:`VerifyResult`. On success, ``counter`` is the matched step so
        the caller can persist it.
    """
    if not _BASE32_RE.match(secret_base32):
        raise TotpError("secret_base32 is not a valid base32 string")

    cleaned = re.sub(r"\s+", "", code or "")
    if not cleaned.isdigit() or len(cleaned) != TOTP_DIGITS:
        return VerifyResult(ok=False)

    now = at if at is not None else time.time()
    current = current_counter(at=now)
    totp = pyotp.TOTP(secret_base32, digits=TOTP_DIGITS, interval=TOTP_PERIOD_SECONDS)

    # Walk from the oldest permitted step outward so "current-1" and "current"
    # both beat "current+1" in the rare case of a clock-skew overlap. A match
    # at or below ``last_used_counter`` is considered a replay.
    for offset in range(-_VERIFY_WINDOW, _VERIFY_WINDOW + 1):
        step = current + offset
        candidate = totp.at(step * TOTP_PERIOD_SECONDS)
        if _constant_time_eq(candidate, cleaned):
            if last_used_counter is not None and step <= last_used_counter:
                # Replay — reject without revealing which step matched.
                return VerifyResult(ok=False)
            return VerifyResult(ok=True, counter=step)
    return VerifyResult(ok=False)


def _constant_time_eq(a: str, b: str) -> bool:
    """Constant-time string equality — used for the TOTP code compare."""
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a.encode(), b.encode()):
        result |= x ^ y
    return result == 0


# ── Backup codes ─────────────────────────────────────────────────────────────


def generate_backup_code() -> str:
    """Return a fresh backup code in display form ``xxxxx-xxxxx``.

    Uses :func:`secrets.token_urlsafe` — URL-safe base64 of cryptographic
    random bytes. Removes any ``-``/``_`` so the delimiter is unambiguous,
    takes the first 10 chars, and inserts a dash in the middle.
    """
    raw = _secrets.token_urlsafe(_BACKUP_CODE_BYTES).replace("-", "").replace("_", "")
    # token_urlsafe never collapses to < 10 chars for nbytes=7 in practice,
    # but top up from a second draw if that ever happens.
    while len(raw) < 10:
        raw += _secrets.token_urlsafe(_BACKUP_CODE_BYTES).replace("-", "").replace("_", "")
    return f"{raw[:5]}-{raw[5:10]}"


def normalize_backup_code(code: str) -> str:
    """Trim whitespace, uppercase, strip the presentation dash."""
    return re.sub(r"\s+", "", code or "").upper().replace("-", "")
