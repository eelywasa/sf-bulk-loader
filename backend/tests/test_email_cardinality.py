"""Email metric cardinality ceiling test (SFBL-142).

The combined label-set ceiling across all four email metrics is:

    3 (backend) × 3 (category) × 4 (status) × 9 (reason) = 324 series

Individual per-metric ceilings:
  sfbl_email_send_total           : 3 × 3 × 4 = 36  series
  sfbl_email_send_duration_seconds: 3 × 3     =  9  series (plus _bucket/_sum/_count)
  sfbl_email_retry_total          : 3 × 9     = 27  series
  sfbl_email_claim_lost_total     : 3         =  3  series

This test enumerates every valid label combination, increments each metric
once, then scrapes REGISTRY.collect() and asserts that the observed series
counts do not exceed these ceilings.

It also asserts that a raw provider code (e.g. "smtp_5xx") is rejected as a
reason label so raw codes never appear in Prometheus output.
"""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY


# Valid label values — bounded sets only.
VALID_BACKENDS = ["noop", "smtp", "ses"]
VALID_STATUSES = ["sent", "failed", "skipped", "pending"]


def test_email_send_total_cardinality():
    """sfbl_email_send_total must not exceed 3 × 3 × 4 = 36 series."""
    from app.observability.metrics import email_send_total
    from app.services.email.message import EmailCategory

    categories = [c.value for c in EmailCategory]
    assert len(categories) == 3, f"Expected 3 EmailCategory values, got {categories}"

    for backend in VALID_BACKENDS:
        for category in categories:
            for status in VALID_STATUSES:
                email_send_total.labels(
                    backend=backend, category=category, status=status
                ).inc()

    # Scrape and count distinct series for this metric
    series_count = 0
    for metric in REGISTRY.collect():
        if metric.name == "sfbl_email_send_total":
            for sample in metric.samples:
                if sample.name == "sfbl_email_send_total_total":
                    series_count += 1

    ceiling = len(VALID_BACKENDS) * len(categories) * len(VALID_STATUSES)
    assert ceiling == 36
    assert series_count <= ceiling, (
        f"sfbl_email_send_total has {series_count} series; ceiling is {ceiling}"
    )


def test_email_send_duration_seconds_cardinality():
    """sfbl_email_send_duration_seconds must not exceed 3 × 3 = 9 histogram series."""
    from app.observability.metrics import email_send_duration_seconds
    from app.services.email.message import EmailCategory

    categories = [c.value for c in EmailCategory]

    for backend in VALID_BACKENDS:
        for category in categories:
            email_send_duration_seconds.labels(backend=backend, category=category).observe(0.5)

    # Count distinct (backend, category) bucket series
    observed_label_sets: set[tuple[str, str]] = set()
    for metric in REGISTRY.collect():
        if metric.name == "sfbl_email_send_duration_seconds":
            for sample in metric.samples:
                if sample.name == "sfbl_email_send_duration_seconds_count":
                    key = (sample.labels["backend"], sample.labels["category"])
                    observed_label_sets.add(key)

    ceiling = len(VALID_BACKENDS) * len(categories)
    assert ceiling == 9
    assert len(observed_label_sets) <= ceiling, (
        f"sfbl_email_send_duration_seconds has {len(observed_label_sets)} distinct label sets; ceiling is {ceiling}"
    )


def test_email_retry_total_cardinality():
    """sfbl_email_retry_total must not exceed 3 × 9 = 27 series."""
    from app.observability.metrics import email_retry_total
    from app.services.email.errors import EmailErrorReason

    reasons = [r.value for r in EmailErrorReason]
    assert len(reasons) == 9, f"Expected 9 EmailErrorReason values, got {reasons}"

    for backend in VALID_BACKENDS:
        for reason in reasons:
            email_retry_total.labels(backend=backend, reason=reason).inc()

    series_count = 0
    for metric in REGISTRY.collect():
        if metric.name == "sfbl_email_retry_total":
            for sample in metric.samples:
                if sample.name == "sfbl_email_retry_total_total":
                    series_count += 1

    ceiling = len(VALID_BACKENDS) * len(reasons)
    assert ceiling == 27
    assert series_count <= ceiling, (
        f"sfbl_email_retry_total has {series_count} series; ceiling is {ceiling}"
    )


def test_email_claim_lost_total_cardinality():
    """sfbl_email_claim_lost_total must not exceed 3 series (one per backend)."""
    from app.observability.metrics import email_claim_lost_total

    for backend in VALID_BACKENDS:
        email_claim_lost_total.labels(backend=backend).inc()

    series_count = 0
    for metric in REGISTRY.collect():
        if metric.name == "sfbl_email_claim_lost_total":
            for sample in metric.samples:
                if sample.name == "sfbl_email_claim_lost_total_total":
                    series_count += 1

    assert series_count <= 3, (
        f"sfbl_email_claim_lost_total has {series_count} series; ceiling is 3"
    )


def test_combined_ceiling_324():
    """Sum of all observed distinct (metric, labels) tuples must not exceed 324."""
    from app.observability.metrics import (
        email_claim_lost_total,
        email_retry_total,
        email_send_duration_seconds,
        email_send_total,
    )
    from app.services.email.errors import EmailErrorReason
    from app.services.email.message import EmailCategory

    categories = [c.value for c in EmailCategory]
    reasons = [r.value for r in EmailErrorReason]

    # Increment every valid combination
    for backend in VALID_BACKENDS:
        for category in categories:
            for status in VALID_STATUSES:
                email_send_total.labels(backend=backend, category=category, status=status).inc()
            email_send_duration_seconds.labels(backend=backend, category=category).observe(1.0)
        for reason in reasons:
            email_retry_total.labels(backend=backend, reason=reason).inc()
        email_claim_lost_total.labels(backend=backend).inc()

    # Collect all distinct (metric_family, labels_tuple) pairs
    distinct_series: set[tuple[str, tuple]] = set()
    target_metrics = {
        "sfbl_email_send_total",
        "sfbl_email_send_duration_seconds",
        "sfbl_email_retry_total",
        "sfbl_email_claim_lost_total",
    }
    for metric in REGISTRY.collect():
        if metric.name in target_metrics:
            for sample in metric.samples:
                # Only count _total or _count samples to avoid bucket explosion
                if sample.name.endswith("_total") or sample.name.endswith("_count"):
                    labels_key = tuple(sorted(sample.labels.items()))
                    distinct_series.add((metric.name, labels_key))

    assert len(distinct_series) <= 324, (
        f"Combined email metric series = {len(distinct_series)}; ceiling is 324"
    )


def test_negative_raw_provider_code_rejected():
    """_assert_email_reason must reject raw provider codes (e.g. 'smtp_5xx').

    Raw provider codes must never appear as metric labels — they belong only
    in span attributes and sanitised log messages.
    """
    from app.observability.metrics import _assert_email_reason

    with pytest.raises(ValueError, match="not a valid EmailErrorReason"):
        _assert_email_reason("smtp_5xx")

    with pytest.raises(ValueError, match="not a valid EmailErrorReason"):
        _assert_email_reason("SES:Throttling")

    with pytest.raises(ValueError, match="not a valid EmailErrorReason"):
        _assert_email_reason("421")

    # Valid values must pass
    assert _assert_email_reason("transient_network") == "transient_network"
    assert _assert_email_reason("permanent_reject") == "permanent_reject"
    assert _assert_email_reason("unknown") == "unknown"
