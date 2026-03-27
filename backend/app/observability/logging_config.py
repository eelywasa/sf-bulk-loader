"""Centralized logging configuration for the Salesforce Bulk Loader backend.

Call `configure_logging(settings)` once at application startup. All subsequent
`logging.getLogger(__name__)` calls across every module will inherit the
configured level and formatter automatically.

Log format is controlled by `settings.log_format`:
  - "plain" — human-readable text for local development
  - "json"  — one JSON object per line, suitable for stdout/stderr collection
               in deployed self-hosted environments

Structured JSON records always include the required common fields defined in
the observability baseline spec:
  timestamp, level, logger, message, service, env

Additional contextual fields (event_name, outcome_code, request_id, run_id,
step_id, job_record_id, sf_job_id, load_plan_id, input_connection_id) are
passed through automatically when present on the LogRecord via the logging
`extra={}` kwarg. This makes the formatter forward-compatible with the context
propagation work planned for later observability tickets.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings

# Fields that are always emitted first in JSON output (ordered for readability).
_JSON_CORE_FIELDS = ("timestamp", "level", "logger", "message", "service", "env")

# Fields that are forwarded from LogRecord.extra when present.
_PASSTHROUGH_FIELDS = (
    "event_name",
    "outcome_code",
    "request_id",
    "run_id",
    "step_id",
    "job_record_id",
    "sf_job_id",
    "load_plan_id",
    "input_connection_id",
    "route",
    "method",
    "duration_ms",
)

# Attributes that belong to LogRecord itself and must not be treated as extras.
_LOGRECORD_STDLIB_ATTRS: frozenset[str] = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()
    | {"message", "asctime"}
)


class _PlainFormatter(logging.Formatter):
    """Human-readable formatter for local development."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


class _JsonFormatter(logging.Formatter):
    """Structured JSON formatter for deployed environments.

    Emits one JSON object per line. Core fields come first; any recognised
    observability pass-through fields follow when present.
    """

    def __init__(self, service: str, env: str) -> None:
        super().__init__()
        self._service = service
        self._env = env

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)

        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()

        payload: dict[str, object] = {
            "timestamp": ts,
            "level": record.levelname,
            "logger": record.name,
            "message": record.message,
            "service": self._service,
            "env": self._env,
        }

        for field in _PASSTHROUGH_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value

        if record.exc_text:
            payload["exception"] = record.exc_text

        return json.dumps(payload, default=str)


def configure_logging(settings: "Settings") -> None:
    """Apply centralized logging configuration.

    Safe to call multiple times — each call replaces the root handler set,
    so duplicate handlers do not accumulate.

    Args:
        settings: The application Settings instance. Reads `log_level`,
                  `log_format`, `service_name`, and `app_env`.
    """
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    if settings.log_format == "json":
        formatter: logging.Formatter = _JsonFormatter(
            service=settings.service_name,
            env=settings.app_env,
        )
    else:
        formatter = _PlainFormatter()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    # Replace any existing handlers so repeated calls stay idempotent.
    root.handlers = [handler]
