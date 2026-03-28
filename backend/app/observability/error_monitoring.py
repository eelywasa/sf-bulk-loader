"""Optional Sentry-based error monitoring integration.

Disabled by default (error_monitoring_enabled=False in config). When disabled,
all calls to this module are no-ops with zero overhead.

To enable, set in .env:
    ERROR_MONITORING_ENABLED=true
    ERROR_MONITORING_DSN=https://<key>@<org>.ingest.sentry.io/<project>

Sanitization rules (SFBL-60 baseline):
- Authorization headers are redacted before events are sent.
- Keys matching the scrubbed-keys set are replaced with [REDACTED] in request
  headers and extra context.
- Raw CSV data, private keys, tokens, and passwords must never appear in spans
  or exception extras; the before_send hook enforces this.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_enabled = False

_SCRUBBED_KEYS: frozenset[str] = frozenset(
    {
        "authorization",
        "x-salesforce-token",
        "private_key",
        "private-key",
        "encryption_key",
        "encryption-key",
        "password",
        "secret",
        "token",
        "jwt",
        "api_key",
        "api-key",
    }
)


def _scrub_event(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    """Sentry before_send hook — redact sensitive fields before transmission."""
    if request := event.get("request"):
        if headers := request.get("headers"):
            event["request"]["headers"] = {
                k: "[REDACTED]" if k.lower() in _SCRUBBED_KEYS else v
                for k, v in headers.items()
            }
        if data := request.get("data"):
            if isinstance(data, dict):
                event["request"]["data"] = {
                    k: "[REDACTED]" if k.lower() in _SCRUBBED_KEYS else v
                    for k, v in data.items()
                }

    if extra := event.get("extra"):
        event["extra"] = {
            k: "[REDACTED]" if k.lower() in _SCRUBBED_KEYS else v
            for k, v in extra.items()
        }

    return event


def configure_error_monitoring(settings) -> None:
    """Initialise error monitoring from application settings.

    Must be called once at startup. No-op when disabled or DSN is absent.
    """
    global _enabled

    if not getattr(settings, "error_monitoring_enabled", False):
        _enabled = False
        return

    dsn = getattr(settings, "error_monitoring_dsn", None)
    if not dsn:
        logger.warning(
            "error_monitoring_enabled=True but ERROR_MONITORING_DSN is not set. "
            "Error monitoring will not be active."
        )
        _enabled = False
        return

    try:
        import sentry_sdk
    except ImportError:
        logger.warning(
            "error_monitoring_enabled=True but sentry-sdk is not installed. "
            "Install sentry-sdk[fastapi] to enable error monitoring."
        )
        _enabled = False
        return

    sentry_sdk.init(
        dsn=dsn,
        traces_sample_rate=0.0,
        release=getattr(settings, "service_name", "sf-bulk-loader-backend"),
        environment=getattr(settings, "app_env", "production"),
        before_send=_scrub_event,
        default_integrations=True,
        send_default_pii=False,
    )
    _enabled = True
    logger.info("Error monitoring configured and active")


def capture_exception(
    exc: Exception,
    *,
    outcome_code: str | None = None,
) -> None:
    """Capture an exception with workflow correlation context.

    No-op when error monitoring is disabled. Never raises — error monitoring
    must not break application flow.
    """
    if not _enabled:
        return

    try:
        import sentry_sdk

        from app.observability.context import (
            input_connection_id_ctx_var,
            job_record_id_ctx_var,
            load_plan_id_ctx_var,
            request_id_ctx_var,
            run_id_ctx_var,
            sf_job_id_ctx_var,
            step_id_ctx_var,
        )

        with sentry_sdk.new_scope() as scope:
            if run_id := run_id_ctx_var.get():
                scope.set_tag("run_id", run_id)
            if step_id := step_id_ctx_var.get():
                scope.set_tag("step_id", step_id)
            if job_record_id := job_record_id_ctx_var.get():
                scope.set_tag("job_record_id", job_record_id)
            if sf_job_id := sf_job_id_ctx_var.get():
                scope.set_tag("sf_job_id", sf_job_id)
            if request_id := request_id_ctx_var.get():
                scope.set_tag("request_id", request_id)
            if load_plan_id := load_plan_id_ctx_var.get():
                scope.set_tag("load_plan_id", load_plan_id)
            if connection_id := input_connection_id_ctx_var.get():
                scope.set_tag("input_connection_id", connection_id)
            if outcome_code:
                scope.set_tag("outcome_code", outcome_code)

            sentry_sdk.capture_exception(exc)
    except Exception:
        pass
