"""Shared Fernet encryption helpers for storing secrets in the DB."""

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


class EncryptionError(Exception):
    """Raised when encryption/decryption fails."""


def _get_fernet() -> Fernet:
    """Return a Fernet instance backed by the ENCRYPTION_KEY env var."""
    key = settings.encryption_key
    if not key:
        raise EncryptionError("ENCRYPTION_KEY environment variable is not set")
    key_bytes = key.encode() if isinstance(key, str) else key
    return Fernet(key_bytes)


def encrypt_secret(value: str) -> str:
    """Fernet-encrypt a plaintext secret for DB storage.

    Returns:
        URL-safe base64 Fernet token (UTF-8 string) suitable for TEXT columns.
    """
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt_secret(encrypted: str) -> str:
    """Decrypt a Fernet-encrypted secret back to its original plaintext form.

    Raises:
        EncryptionError: If decryption fails (wrong key, tampered token, etc.).
    """
    try:
        return _get_fernet().decrypt(encrypted.encode()).decode()
    except InvalidToken as exc:
        raise EncryptionError(
            "Failed to decrypt secret — verify ENCRYPTION_KEY is correct"
        ) from exc
