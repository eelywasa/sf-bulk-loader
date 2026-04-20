"""Prometheus-compatible metrics registry for the Salesforce Bulk Loader.

All metric objects are module-level singletons registered against the default
``CollectorRegistry``. Import and call the helper functions from service code
to record observations.

Metric conventions (per spec §5):
- Low-cardinality labels only — no entity IDs (run_id, step_id, etc.)
- Counters for totals
- Histograms for duration
- Gauges for point-in-time concurrency / health

Label guidance:
- ``object_name``  — Salesforce object type (e.g. "Account", "Contact")
- ``operation``    — Bulk API operation ("insert", "update", "upsert", "delete")
- ``final_status`` — terminal run/step outcome ("completed", "completed_with_errors",
                     "failed", "aborted")
- ``method``       — HTTP method ("GET", "POST", …)
- ``status_class`` — HTTP response class ("2xx", "4xx", "5xx")
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ── HTTP metrics ───────────────────────────────────────────────────────────────

http_requests_total = Counter(
    "sfbl_http_requests_total",
    "Total number of HTTP requests received.",
    labelnames=["method", "status_class"],
)

http_request_duration_seconds = Histogram(
    "sfbl_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    labelnames=["method"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# ── WebSocket metrics ─────────────────────────────────────────────────────────

ws_active_connections = Gauge(
    "sfbl_ws_active_connections",
    "Number of currently active WebSocket connections.",
)

# ── Run lifecycle counters ────────────────────────────────────────────────────

runs_started_total = Counter(
    "sfbl_runs_started_total",
    "Total number of load runs started.",
)

run_preflight_failures_total = Counter(
    "sfbl_run_preflight_failures_total",
    "Total number of preflight (pre-count) failures across all runs. "
    "A single run may contribute more than one increment (one per failing step).",
    labelnames=["reason"],
)

runs_completed_total = Counter(
    "sfbl_runs_completed_total",
    "Total number of load runs that reached a terminal state.",
    labelnames=["final_status"],
)

run_duration_seconds = Histogram(
    "sfbl_run_duration_seconds",
    "Duration of a load run from start to terminal state, in seconds.",
    labelnames=["final_status"],
    buckets=(5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0, 3600.0),
)

# ── Step lifecycle counters ────────────────────────────────────────────────────

steps_completed_total = Counter(
    "sfbl_steps_completed_total",
    "Total number of load steps that completed (including threshold-exceeded steps).",
    labelnames=["object_name", "operation", "final_status"],
)

step_duration_seconds = Histogram(
    "sfbl_step_duration_seconds",
    "Duration of a load step from start to completion, in seconds.",
    labelnames=["object_name", "operation"],
    buckets=(1.0, 5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0),
)

# ── Record throughput counters ─────────────────────────────────────────────────

records_processed_total = Counter(
    "sfbl_records_processed_total",
    "Total number of records submitted to Salesforce.",
    labelnames=["object_name", "operation"],
)

records_succeeded_total = Counter(
    "sfbl_records_succeeded_total",
    "Total number of records successfully processed by Salesforce.",
    labelnames=["object_name", "operation"],
)

records_failed_total = Counter(
    "sfbl_records_failed_total",
    "Total number of records that failed in Salesforce.",
    labelnames=["object_name", "operation"],
)

# ── Salesforce integration metrics ────────────────────────────────────────────

sf_requests_total = Counter(
    "sfbl_sf_requests_total",
    "Total number of outbound Salesforce API requests.",
    labelnames=["operation"],
)

sf_request_duration_seconds = Histogram(
    "sfbl_sf_request_duration_seconds",
    "Salesforce API request duration in seconds.",
    labelnames=["operation"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

sf_retries_total = Counter(
    "sfbl_sf_retries_total",
    "Total number of Salesforce API request retries.",
    labelnames=["reason"],
)

sf_rate_limited_total = Counter(
    "sfbl_sf_rate_limited_total",
    "Total number of Salesforce rate-limit (429) responses received.",
)

bulk_job_poll_timeout_total = Counter(
    "sfbl_bulk_job_poll_timeout_total",
    "Total number of Bulk API jobs that exceeded the configured poll timeout "
    "(sf_job_max_poll_seconds) and were marked failed by the client.",
)

# ── Bulk query metrics (SFBL-171) ─────────────────────────────────────────────
#
# Query jobs are distinct from DML ingest jobs.  These metrics cover the Bulk
# API 2.0 query path (query / queryAll operations) in bulk_query_executor.py.
#
# Labels:
#   object_name — Salesforce object type (e.g. "Account", "Contact")
#   operation   — "query" or "queryAll"
#
# Cardinality: object_name × operation is low-cardinality (bounded by the
# number of distinct Salesforce object types referenced in load plans).

bulk_query_jobs_created_total = Counter(
    "sfbl_bulk_query_jobs_created_total",
    "Total number of Bulk API 2.0 query jobs created.",
    labelnames=["object_name", "operation"],
)

bulk_query_jobs_completed_total = Counter(
    "sfbl_bulk_query_jobs_completed_total",
    "Total number of Bulk API 2.0 query jobs that reached JobComplete state.",
    labelnames=["object_name", "operation"],
)

bulk_query_jobs_failed_total = Counter(
    "sfbl_bulk_query_jobs_failed_total",
    "Total number of Bulk API 2.0 query jobs that reached Failed or Aborted state.",
    labelnames=["object_name", "operation"],
)

bulk_query_rows_histogram = Histogram(
    "sfbl_bulk_query_rows",
    "Number of data rows returned per completed query step.",
    labelnames=["object_name", "operation"],
    buckets=(0, 100, 1_000, 10_000, 100_000, 500_000, 1_000_000, 5_000_000),
)

bulk_query_bytes_histogram = Histogram(
    "sfbl_bulk_query_bytes",
    "Total bytes written per completed query artefact.",
    labelnames=["object_name", "operation"],
    buckets=(
        1_024,        # 1 KiB
        10_240,       # 10 KiB
        102_400,      # 100 KiB
        1_048_576,    # 1 MiB
        10_485_760,   # 10 MiB
        104_857_600,  # 100 MiB
        1_073_741_824,  # 1 GiB
    ),
)

bulk_query_locator_pages_histogram = Histogram(
    "sfbl_bulk_query_locator_pages",
    "Number of Sforce-Locator pagination pages per completed query step.",
    labelnames=["object_name", "operation"],
    buckets=(1, 2, 5, 10, 25, 50, 100, 250, 500),
)


# ── Helper functions ──────────────────────────────────────────────────────────


def record_run_started() -> None:
    runs_started_total.inc()


def record_run_preflight_failure(reason: str) -> None:
    """Increment the preflight-failure counter.

    ``reason`` is a low-cardinality label — use canonical ``OutcomeCode`` values
    (e.g. ``"storage_error"``, ``"unexpected_exception"``).
    """
    run_preflight_failures_total.labels(reason=reason).inc()


# ── Email delivery metrics ────────────────────────────────────────────────────
#
# Cardinality ceiling: 3 (backend) × 3 (category) × 4 (status) × 9 (reason)
# = 324 series maximum across all four email metrics combined.
#
# backend values : "noop" | "smtp" | "ses"
# category values: EmailCategory enum — "auth" | "notification" | "system"
# status values  : "sent" | "failed" | "skipped" | "pending"
# reason values  : EmailErrorReason enum — exactly 9 values

email_send_total = Counter(
    "sfbl_email_send_total",
    "Number of email send attempts, by backend / category / status.",
    ["backend", "category", "status"],
)

email_send_duration_seconds = Histogram(
    "sfbl_email_send_duration_seconds",
    "Duration of email send attempts in seconds.",
    ["backend", "category"],
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 15.0, 30.0),
)

email_retry_total = Counter(
    "sfbl_email_retry_total",
    "Number of email retry attempts classified by failure reason.",
    ["backend", "reason"],
)

email_claim_lost_total = Counter(
    "sfbl_email_claim_lost_total",
    "Number of email deliveries where a retry task lost the CAS claim to another worker.",
    ["backend"],
)


# ── Email metric helpers ──────────────────────────────────────────────────────


# ── Auth / password-reset + email-change counters (SFBL-151) ─────────────────
#
# Cardinality: each counter has a single ``outcome`` label drawn from the fixed
# OutcomeCode enum values used in the auth flows.  Maximum cardinality per counter
# is ~6 values — well within the low-cardinality ceiling.

auth_password_reset_requests_total = Counter(
    "sfbl_auth_password_reset_requests_total",
    "Total password-reset request attempts, by outcome.",
    ["outcome"],
)

auth_password_reset_confirms_total = Counter(
    "sfbl_auth_password_reset_confirms_total",
    "Total password-reset confirmation attempts, by outcome.",
    ["outcome"],
)

auth_password_changes_total = Counter(
    "sfbl_auth_password_changes_total",
    "Total authenticated password-change attempts, by outcome.",
    ["outcome"],
)

auth_email_change_requests_total = Counter(
    "sfbl_auth_email_change_requests_total",
    "Total email-change request attempts, by outcome.",
    ["outcome"],
)

auth_email_change_confirms_total = Counter(
    "sfbl_auth_email_change_confirms_total",
    "Total email-change confirmation attempts, by outcome.",
    ["outcome"],
)


# ── Auth metric helpers ───────────────────────────────────────────────────────


def record_auth_password_reset_request(outcome: str) -> None:
    """Increment the password-reset request counter."""
    auth_password_reset_requests_total.labels(outcome=outcome).inc()


def record_auth_password_reset_confirm(outcome: str) -> None:
    """Increment the password-reset confirmation counter."""
    auth_password_reset_confirms_total.labels(outcome=outcome).inc()


def record_auth_password_change(outcome: str) -> None:
    """Increment the authenticated password-change counter."""
    auth_password_changes_total.labels(outcome=outcome).inc()


def record_auth_email_change_request(outcome: str) -> None:
    """Increment the email-change request counter."""
    auth_email_change_requests_total.labels(outcome=outcome).inc()


def record_auth_email_change_confirm(outcome: str) -> None:
    """Increment the email-change confirmation counter."""
    auth_email_change_confirms_total.labels(outcome=outcome).inc()


def _assert_email_reason(reason: str) -> str:
    """Validate that *reason* is a member of EmailErrorReason enum.

    Raises ValueError for any raw provider code (e.g. ``"smtp_5xx"``) that
    has not been normalised through the classification table. Raw provider codes
    must never appear as metric labels — they belong in span attributes and logs.
    """
    # Import lazily to avoid a module-level circular import.
    from app.services.email.errors import EmailErrorReason

    if reason not in {r.value for r in EmailErrorReason}:
        raise ValueError(
            f"reason {reason!r} is not a valid EmailErrorReason value. "
            "Normalise via backend.classify() before recording metrics."
        )
    return reason


def record_bulk_job_poll_timeout() -> None:
    """Increment the Bulk API job poll-timeout counter (SFBL-111)."""
    bulk_job_poll_timeout_total.inc()


# ── Bulk query metric helpers (SFBL-171) ──────────────────────────────────────


def record_bulk_query_job_created(object_name: str, operation: str) -> None:
    """Increment the bulk-query job-created counter."""
    bulk_query_jobs_created_total.labels(
        object_name=object_name, operation=operation
    ).inc()


def record_bulk_query_job_completed(
    object_name: str,
    operation: str,
    row_count: int,
    byte_count: int,
    page_count: int,
) -> None:
    """Increment the bulk-query completed counter and record size histograms.

    Args:
        object_name: Salesforce object type (low-cardinality label).
        operation:   ``"query"`` or ``"queryAll"`` (low-cardinality label).
        row_count:   Number of data rows (header excluded) in the result file.
        byte_count:  Total bytes written to the output artefact.
        page_count:  Number of Sforce-Locator pages fetched (≥ 1).
    """
    bulk_query_jobs_completed_total.labels(
        object_name=object_name, operation=operation
    ).inc()
    bulk_query_rows_histogram.labels(
        object_name=object_name, operation=operation
    ).observe(row_count)
    bulk_query_bytes_histogram.labels(
        object_name=object_name, operation=operation
    ).observe(byte_count)
    bulk_query_locator_pages_histogram.labels(
        object_name=object_name, operation=operation
    ).observe(page_count)


def record_bulk_query_job_failed(object_name: str, operation: str) -> None:
    """Increment the bulk-query job-failed counter."""
    bulk_query_jobs_failed_total.labels(
        object_name=object_name, operation=operation
    ).inc()


def record_run_completed(final_status: str, duration_seconds: float) -> None:
    runs_completed_total.labels(final_status=final_status).inc()
    run_duration_seconds.labels(final_status=final_status).observe(duration_seconds)


def record_step_completed(
    object_name: str,
    operation: str,
    final_status: str,
    duration_seconds: float,
    records_processed: int,
    records_succeeded: int,
    records_failed: int,
) -> None:
    steps_completed_total.labels(
        object_name=object_name, operation=operation, final_status=final_status
    ).inc()
    step_duration_seconds.labels(
        object_name=object_name, operation=operation
    ).observe(duration_seconds)
    records_processed_total.labels(
        object_name=object_name, operation=operation
    ).inc(records_processed)
    records_succeeded_total.labels(
        object_name=object_name, operation=operation
    ).inc(records_succeeded)
    records_failed_total.labels(
        object_name=object_name, operation=operation
    ).inc(records_failed)
