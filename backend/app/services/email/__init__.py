"""Outbound email service package.

Public API re-exports for consumers:

    from app.services.email import (
        EmailService,
        EmailMessage,
        EmailCategory,
        EmailError,
        EmailRenderError,
        EmailErrorReason,
    )

See service.py for `build_email_service`, `init_email_service`, and the
FastAPI `get_email_service` dependency.
"""

from app.services.email.errors import EmailError, EmailErrorReason, EmailRenderError
from app.services.email.message import EmailCategory, EmailMessage
from app.services.email.service import EmailService, build_email_service, get_email_service, init_email_service

__all__ = [
    "EmailCategory",
    "EmailError",
    "EmailErrorReason",
    "EmailMessage",
    "EmailRenderError",
    "EmailService",
    "build_email_service",
    "get_email_service",
    "init_email_service",
]
