"""Admin email API — test-send endpoint.

POST /api/admin/email/test

Requires admin auth (hosted profiles only). Returns 404 on desktop
(auth_mode=none) — the router is not registered at all in that profile.

Response shapes
---------------
200 success:
    { "status": "sent"|"skipped", "delivery_id": str,
      "provider_message_id": str|null, "backend": str }

200 pending (first attempt failed transiently; retry scheduled):
    { "status": "pending"|"sending", "delivery_id": str, "attempts": int,
      "reason": str|null, "last_error_msg": str|null, "backend": str }

200 backend failure (typed, not an HTTP error — UI can render without error-boundary):
    { "status": "failed", "delivery_id": str,
      "reason": str, "last_error_msg": str|null, "backend": str }

422 render failure:
    { "code": str, "message": str }

400 bad request (invalid email, unknown template):
    FastAPI HTTPException detail string.

401/403 via existing auth dependency.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.models.user import User
from app.services.auth import get_current_user
from app.services.email.errors import EmailRenderError
from app.services.email.message import EmailCategory
from app.services.email.service import EmailService, get_email_service

router = APIRouter(prefix="/api/admin/email", tags=["admin-email"])

# ── Supported test templates ──────────────────────────────────────────────────

_VALID_TEMPLATES: frozenset[str] = frozenset(
    {
        "auth/password_reset",
        "auth/email_change_verify",
        "notifications/run_complete",
    }
)

_TEMPLATE_CONTEXT: dict[str, dict[str, Any]] = {
    "auth/password_reset": {
        "user_display_name": "Test User",
        "reset_url": "https://example.invalid/test-reset",
        "expires_in_minutes": 60,
    },
    "auth/email_change_verify": {
        "user_display_name": "Test User",
        "confirm_url": "https://example.invalid/test-confirm",
        "new_email": "test-new@example.invalid",
        "expires_in_minutes": 60,
    },
    "notifications/run_complete": {
        "plan_name": "Test Plan",
        "run_id": "test-run-0000",
        "status": "succeeded",
        "total_rows": 0,
        "success_rows": 0,
        "failed_rows": 0,
        "started_at": "2026-01-01T00:00:00Z",
        "ended_at": "2026-01-01T00:01:00Z",
        "run_url": "https://example.invalid/runs/test",
    },
}

_TEMPLATE_CATEGORY: dict[str, EmailCategory] = {
    "auth/password_reset": EmailCategory.AUTH,
    "auth/email_change_verify": EmailCategory.AUTH,
    "notifications/run_complete": EmailCategory.NOTIFICATION,
}

# Simple RFC-5322-ish email validation (rejects obvious non-emails)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ── Request / response schemas ────────────────────────────────────────────────


class EmailTestRequest(BaseModel):
    to: str
    template: str


class EmailTestSuccessResponse(BaseModel):
    status: Literal["sent", "skipped"]
    delivery_id: str
    provider_message_id: str | None
    backend: str


class EmailTestFailureResponse(BaseModel):
    status: Literal["failed"]
    delivery_id: str
    reason: str
    last_error_msg: str | None
    backend: str


class EmailTestPendingResponse(BaseModel):
    """First attempt failed transiently; a retry is scheduled in the background.

    Returned when the send is still in flight after send_template() returns —
    i.e. the row is pending or sending. The caller can poll the delivery log
    to watch it advance to sent/failed.
    """

    status: Literal["pending", "sending"]
    delivery_id: str
    attempts: int
    reason: str | None
    last_error_msg: str | None
    backend: str


class EmailTestRenderFailureResponse(BaseModel):
    code: str
    message: str


# ── Admin dependency ─────────────────────────────────────────────────────────


def require_admin(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    """Dependency that requires the current user to have the 'admin' role."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return current_user


# ── Endpoint ──────────────────────────────────────────────────────────────────


@router.post(
    "/test",
    response_model=None,
    summary="Send a test email via the configured backend",
)
async def admin_email_test(
    body: EmailTestRequest,
    _admin: Annotated[User, Depends(require_admin)],
    email_service: Annotated[EmailService, Depends(get_email_service)],
) -> Any:
    """Send a test email using a fixed fixture context.

    Returns a 200 response in all non-error cases (including backend send
    failures) so the UI can render the typed result without error-boundary
    gymnastics.

    Raises 422 on render failure, 400 on bad input.
    """
    from fastapi.responses import JSONResponse

    # Validate email address
    if not _EMAIL_RE.match(body.to.strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email address in 'to' field",
        )

    # Validate template name
    if body.template not in _VALID_TEMPLATES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unknown template {body.template!r}. "
                f"Valid options: {sorted(_VALID_TEMPLATES)}"
            ),
        )

    context = _TEMPLATE_CONTEXT[body.template]
    category = _TEMPLATE_CATEGORY[body.template]

    try:
        delivery = await email_service.send_template(
            body.template,
            context,
            to=body.to.strip(),
            category=category,
        )
    except EmailRenderError as exc:
        # 422 with stable code, generic message — never leak offending value
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=EmailTestRenderFailureResponse(
                code=exc.code,
                message="Template render failed. Check the template configuration.",
            ).model_dump(),
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while sending the test email.",
        )

    # Map delivery status to response. DeliveryStatus is a StrEnum so we
    # normalise to its string value before passing into Pydantic Literal fields.
    status_value = (
        delivery.status.value if hasattr(delivery.status, "value") else str(delivery.status)
    )

    if status_value == "failed":
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=EmailTestFailureResponse(
                status="failed",
                delivery_id=str(delivery.id),
                reason=delivery.last_error_code or "unknown",
                last_error_msg=delivery.last_error_msg,
                backend=delivery.backend,
            ).model_dump(),
        )

    if status_value in ("pending", "sending"):
        # First attempt failed transiently; a retry is scheduled. Don't 500 —
        # report pending so the UI can surface "queued, check delivery log".
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=EmailTestPendingResponse(
                status=status_value,  # type: ignore[arg-type]
                delivery_id=str(delivery.id),
                attempts=delivery.attempts,
                reason=delivery.last_error_code,
                last_error_msg=delivery.last_error_msg,
                backend=delivery.backend,
            ).model_dump(),
        )

    # sent or skipped
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=EmailTestSuccessResponse(
            status=status_value,  # type: ignore[arg-type]
            delivery_id=str(delivery.id),
            provider_message_id=delivery.provider_message_id,
            backend=delivery.backend,
        ).model_dump(),
    )
