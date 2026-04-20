"""Optional OpenTelemetry-compatible tracing for workflow execution boundaries.

Tracing is disabled by default (tracing_enabled=False in config). When disabled,
a NoOpTracerProvider is installed so all span calls are zero-overhead no-ops.

When enabled, a real TracerProvider is set up with optional OTLP export.
FastAPI and httpx are auto-instrumented, and custom workflow spans are created
around run/step/partition execution boundaries.

Usage in execution services:

    from app.observability import tracing

    with tracing.run_span(run_id, load_plan_id) as span:
        span.set_attribute("outcome.code", outcome_code)
        ...

    with tracing.step_span(step_id, object_name, operation) as span:
        ...

    with tracing.partition_span(job_record_id) as span:
        span.set_attribute("salesforce.job.id", sf_job_id)  # once known
        ...
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

from opentelemetry import trace
from opentelemetry.trace import Span

from app.observability.sanitization import safe_record_exception

logger = logging.getLogger(__name__)

_configured = False


def configure_tracing(settings) -> None:
    """Set up the global TracerProvider from application settings.

    Must be called once at startup before the FastAPI app is created.
    Safe to call multiple times (idempotent after first call).
    """
    global _configured
    if _configured:
        return
    _configured = True

    if not settings.tracing_enabled:
        from opentelemetry.trace import NoOpTracerProvider

        trace.set_tracer_provider(NoOpTracerProvider())
        logger.debug("Tracing disabled — NoOpTracerProvider installed")
        return

    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON, TraceIdRatioBased

    ratio = getattr(settings, "trace_sample_ratio", 1.0)
    sampler = ALWAYS_ON if ratio >= 1.0 else TraceIdRatioBased(ratio)
    provider = TracerProvider(sampler=sampler)

    otlp_endpoint = getattr(settings, "otlp_endpoint", None)
    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            logger.info("Tracing: OTLP exporter configured for %s", otlp_endpoint)
        except ImportError:
            logger.warning(
                "otlp_endpoint is set but opentelemetry-exporter-otlp is not installed. "
                "Install opentelemetry-exporter-otlp to export traces."
            )

    trace.set_tracer_provider(provider)

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
        logger.debug("Tracing: httpx auto-instrumented")
    except Exception as exc:
        logger.warning("Tracing: httpx instrumentation failed: %s", exc)

    logger.info(
        "Tracing enabled (sample_ratio=%.2f, otlp=%s)",
        ratio,
        otlp_endpoint or "none",
    )


def instrument_fastapi_app(app) -> None:
    """Instrument a FastAPI app with OpenTelemetry middleware.

    Must be called after the FastAPI app instance is created.
    Does nothing when tracing is disabled.
    """
    if isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider):
        return
    try:
        from opentelemetry.trace import NoOpTracerProvider

        if isinstance(trace.get_tracer_provider(), NoOpTracerProvider):
            return
    except Exception:
        return

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        logger.debug("Tracing: FastAPI auto-instrumented")
    except Exception as exc:
        logger.warning("Tracing: FastAPI instrumentation failed: %s", exc)


def _get_tracer() -> trace.Tracer:
    return trace.get_tracer("sf-bulk-loader")


@contextmanager
def run_span(run_id: str, load_plan_id: str) -> Generator[Span, None, None]:
    """Context manager that creates a span for a full run execution."""
    tracer = _get_tracer()
    with tracer.start_as_current_span("run.execute") as span:
        span.set_attribute("run.id", run_id)
        span.set_attribute("load_plan.id", load_plan_id)
        try:
            yield span
        except Exception as exc:
            safe_record_exception(span, exc)
            raise


@contextmanager
def step_span(
    step_id: str, object_name: str, operation: str
) -> Generator[Span, None, None]:
    """Context manager that creates a span for a single step execution."""
    tracer = _get_tracer()
    with tracer.start_as_current_span("step.execute") as span:
        span.set_attribute("step.id", step_id)
        span.set_attribute("object.name", object_name)
        span.set_attribute("operation", operation)
        try:
            yield span
        except Exception as exc:
            safe_record_exception(span, exc)
            raise


@contextmanager
def email_send_span(
    *,
    backend: str,
    category: str,
    template: str | None,
    to_domain: str,
    attempt: int,
) -> Generator[Span, None, None]:
    """Context manager that creates a span for a single email send attempt.

    The caller may set additional attributes on the yielded span after the
    send completes:

    - ``email.reason`` — the ``EmailErrorReason`` value on failure; omit on success.
    - ``email.provider_error_code`` — raw provider error code on failure
      (e.g. ``"SES:Throttling"``, ``"SMTP:421"``). **Span-only** — never a
      metric label. Omit on success.

    Example::

        with tracing.email_send_span(
            backend="smtp",
            category="auth",
            template="auth/password_reset",
            to_domain="example.com",
            attempt=1,
        ) as span:
            result = await backend.send(msg)
            if not result["accepted"]:
                span.set_attribute("email.reason", result["reason"].value)
                if result.get("error_detail"):
                    span.set_attribute("email.provider_error_code", result["error_detail"])
    """
    tracer = _get_tracer()
    with tracer.start_as_current_span("email.send") as span:
        span.set_attribute("email.backend", backend)
        span.set_attribute("email.category", category)
        if template is not None:
            span.set_attribute("email.template", template)
        span.set_attribute("email.to_domain", to_domain)
        span.set_attribute("email.attempt", attempt)
        try:
            yield span
        except Exception as exc:
            safe_record_exception(span, exc)
            raise


@contextmanager
def auth_password_reset_request_span() -> Generator[Span, None, None]:
    """Context manager for a password-reset request operation.

    Attributes set by caller after resolution:
    - ``outcome`` — OutcomeCode value (e.g. ``"sent"``, ``"rate_limited"``)

    Never attach email addresses or raw tokens as span attributes.
    """
    tracer = _get_tracer()
    with tracer.start_as_current_span("auth.password_reset.request") as span:
        try:
            yield span
        except Exception as exc:
            safe_record_exception(span, exc)
            raise


@contextmanager
def auth_password_reset_confirm_span(*, user_id: str | None = None) -> Generator[Span, None, None]:
    """Context manager for a password-reset confirmation operation.

    Attributes set by caller after resolution:
    - ``outcome`` — OutcomeCode value

    ``user_id`` may be set once the associated user is resolved (before any
    exception is raised). Never attach raw tokens.
    """
    tracer = _get_tracer()
    with tracer.start_as_current_span("auth.password_reset.confirm") as span:
        if user_id is not None:
            span.set_attribute("user.id", user_id)
        try:
            yield span
        except Exception as exc:
            safe_record_exception(span, exc)
            raise


@contextmanager
def auth_email_change_request_span(*, user_id: str | None = None) -> Generator[Span, None, None]:
    """Context manager for an email-change request operation.

    Attributes set by caller after resolution:
    - ``outcome`` — OutcomeCode value
    """
    tracer = _get_tracer()
    with tracer.start_as_current_span("auth.email_change.request") as span:
        if user_id is not None:
            span.set_attribute("user.id", user_id)
        try:
            yield span
        except Exception as exc:
            safe_record_exception(span, exc)
            raise


@contextmanager
def auth_email_change_confirm_span() -> Generator[Span, None, None]:
    """Context manager for an email-change confirmation operation.

    Attributes set by caller after resolution:
    - ``outcome`` — OutcomeCode value
    - ``user.id`` — once the token's user is resolved

    Never attach email addresses or raw tokens.
    """
    tracer = _get_tracer()
    with tracer.start_as_current_span("auth.email_change.confirm") as span:
        try:
            yield span
        except Exception as exc:
            safe_record_exception(span, exc)
            raise


@contextmanager
def auth_password_change_span(*, user_id: str | None = None) -> Generator[Span, None, None]:
    """Context manager for an authenticated password-change operation.

    Attributes set by caller after resolution:
    - ``outcome`` — OutcomeCode value
    """
    tracer = _get_tracer()
    with tracer.start_as_current_span("auth.password_change") as span:
        if user_id is not None:
            span.set_attribute("user.id", user_id)
        try:
            yield span
        except Exception as exc:
            safe_record_exception(span, exc)
            raise


@contextmanager
def bulk_query_span(
    *,
    object_name: str,
    operation: str,
    sf_job_id: str | None = None,
) -> Generator[Span, None, None]:
    """Context manager that creates a span for a full bulk-query executor invocation.

    Wraps the entire :func:`~app.services.bulk_query_executor.run_bulk_query`
    call — from job creation through to the last page streamed to output storage.

    The caller should set ``salesforce.job.id`` once the job ID is known::

        with tracing.bulk_query_span(object_name="Account", operation="query") as span:
            result = await run_bulk_query(...)
            span.set_attribute("salesforce.job.id", result.sf_job_id)

    Attributes set automatically:
        - ``object.name``   — Salesforce object type
        - ``operation``     — ``"query"`` or ``"queryAll"``
        - ``salesforce.job.id`` — set if *sf_job_id* is provided; may also be
          set by the caller after the job is created.

    SOQL must not be set as a span attribute (it may contain WHERE-clause values
    that are quasi-identifiers). Log it via
    :func:`~app.observability.sanitization.sanitize_soql` at DEBUG level only.
    """
    tracer = _get_tracer()
    with tracer.start_as_current_span("bulk_query.execute") as span:
        span.set_attribute("object.name", object_name)
        span.set_attribute("operation", operation)
        if sf_job_id is not None:
            span.set_attribute("salesforce.job.id", sf_job_id)
        try:
            yield span
        except Exception as exc:
            safe_record_exception(span, exc)
            raise


@contextmanager
def partition_span(job_record_id: str) -> Generator[Span, None, None]:
    """Context manager that creates a span for a single partition/job execution.

    The caller should set ``salesforce.job.id`` on the returned span once the
    Salesforce job ID is known::

        with tracing.partition_span(job_record_id) as span:
            sf_job_id = await bulk_client.create_job(...)
            span.set_attribute("salesforce.job.id", sf_job_id)
    """
    tracer = _get_tracer()
    with tracer.start_as_current_span("partition.execute") as span:
        span.set_attribute("job_record.id", job_record_id)
        try:
            yield span
        except Exception as exc:
            safe_record_exception(span, exc)
            raise
