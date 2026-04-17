"""Email backend implementations.

Available backends:
    noop  — NoopBackend (always accepted=True, records status=skipped)
    smtp  — SmtpBackend (SFBL-139, not yet implemented)
    ses   — SesBackend  (SFBL-140, not yet implemented)
"""

from app.services.email.backends.base import BackendResult, EmailBackend
from app.services.email.backends.noop import NoopBackend

__all__ = ["BackendResult", "EmailBackend", "NoopBackend"]
