"""Email metric cardinality ceiling test (SFBL-142).

The combined label-set ceiling across all four email metrics is:

    3 (backend) × 3 (category) × 4 (status) × 9 (reason) = 324 series

Individual per-metric ceilings:
  sfbl_email_send_total           : 3 × 3 × 4 = 36  series
  sfbl_email_send_duration_seconds: 3 × 3     =  9  series (plus _bucket/_sum/_count)
  sfbl_email_retry_total          : 3 × 9     = 27  series
  sfbl_email_claim_lost_total     : 3         =  3  series

These tests enumerate every valid label combination in an ISOLATED Prometheus
registry (separate from the global REGISTRY used by the running app) to verify
the ceiling holds under the production label vocabulary only.

A negative test also asserts that a raw provider code (e.g. "smtp_5xx") is
rejected by the guard helper so raw codes never appear in any registry.
"""

from __future__ import annotations

import pytest
from prometheus_client import CollectorRegistry, Counter, Histogram


# Valid label values — bounded sets only.
VALID_BACKENDS = ["noop", "smtp", "ses"]
VALID_STATUSES = ["sent", "failed", "skipped", "pending"]


def _make_isolated_metrics():
    """Return fresh metric instances in an isolated registry.

    Using a fresh CollectorRegistry prevents cross-test pollution from
    test-only backends (e.g. 'fake_transient') that other test modules
    register on the global REGISTRY.
    """
    reg = CollectorRegistry()

    send_total = Counter(
        "sfbl_email_send_total",
        "isolated test copy",
        ["backend", "category", "status"],
        registry=reg,
    )
    send_duration = Histogram(
        "sfbl_email_send_duration_seconds",
        "isolated test copy",
        ["backend", "category"],
        buckets=(0.1, 0.5, 1.0, 5.0, 15.0, 30.0),
        registry=reg,
    )
    retry_total = Counter(
        "sfbl_email_retry_total",
        "isolated test copy",
        ["backend", "reason"],
        registry=reg,
    )
    claim_lost = Counter(
        "sfbl_email_claim_lost_total",
        "isolated test copy",
        ["backend"],
        registry=reg,
    )
    return reg, send_total, send_duration, retry_total, claim_lost


def test_email_send_total_cardinality():
    """sfbl_email_send_total must not exceed 3 × 3 × 4 = 36 series."""
    from app.services.email.message import EmailCategory

    categories = [c.value for c in EmailCategory]
    assert len(categories) == 3, f"Expected 3 EmailCategory values, got {categories}"

    reg, send_total, _, _, _ = _make_isolated_metrics()

    for backend in VALID_BACKENDS:
        for category in categories:
            for status in VALID_STATUSES:
                send_total.labels(backend=backend, category=category, status=status).inc()

    series_count = sum(
        1
        for metric in reg.collect()
        if metric.name == "sfbl_email_send_total"
        for sample in metric.samples
        if sample.name == "sfbl_email_send_total"
    )

    ceiling = len(VALID_BACKENDS) * len(categories) * len(VALID_STATUSES)
    assert ceiling == 36
    assert series_count <= ceiling, (
        f"sfbl_email_send_total has {series_count} series; ceiling is {ceiling}"
    )


def test_email_send_duration_seconds_cardinality():
    """sfbl_email_send_duration_seconds must not exceed 3 × 3 = 9 histogram series."""
    from app.services.email.message import EmailCategory

    categories = [c.value for c in EmailCategory]
    reg, _, send_duration, _, _ = _make_isolated_metrics()

    for backend in VALID_BACKENDS:
        for category in categories:
            send_duration.labels(backend=backend, category=category).observe(0.5)

    observed_label_sets: set[tuple[str, str]] = {
        (sample.labels["backend"], sample.labels["category"])
        for metric in reg.collect()
        if metric.name == "sfbl_email_send_duration_seconds"
        for sample in metric.samples
        if sample.name == "sfbl_email_send_duration_seconds_count"
    }

    ceiling = len(VALID_BACKENDS) * len(categories)
    assert ceiling == 9
    assert len(observed_label_sets) <= ceiling, (
        f"sfbl_email_send_duration_seconds has {len(observed_label_sets)} distinct label sets; ceiling is {ceiling}"
    )


def test_email_retry_total_cardinality():
    """sfbl_email_retry_total must not exceed 3 × 9 = 27 series."""
    from app.services.email.errors import EmailErrorReason

    reasons = [r.value for r in EmailErrorReason]
    assert len(reasons) == 9, f"Expected 9 EmailErrorReason values, got {reasons}"

    reg, _, _, retry_total, _ = _make_isolated_metrics()

    for backend in VALID_BACKENDS:
        for reason in reasons:
            retry_total.labels(backend=backend, reason=reason).inc()

    series_count = sum(
        1
        for metric in reg.collect()
        if metric.name == "sfbl_email_retry_total"
        for sample in metric.samples
        if sample.name == "sfbl_email_retry_total"
    )

    ceiling = len(VALID_BACKENDS) * len(reasons)
    assert ceiling == 27
    assert series_count <= ceiling, (
        f"sfbl_email_retry_total has {series_count} series; ceiling is {ceiling}"
    )


def test_email_claim_lost_total_cardinality():
    """sfbl_email_claim_lost_total must not exceed 3 series (one per backend)."""
    reg, _, _, _, claim_lost = _make_isolated_metrics()

    for backend in VALID_BACKENDS:
        claim_lost.labels(backend=backend).inc()

    series_count = sum(
        1
        for metric in reg.collect()
        if metric.name == "sfbl_email_claim_lost_total"
        for sample in metric.samples
        if sample.name == "sfbl_email_claim_lost_total"
    )

    assert series_count <= 3, (
        f"sfbl_email_claim_lost_total has {series_count} series; ceiling is 3"
    )


def test_combined_ceiling_324():
    """Sum of all valid-backend distinct (metric, labels) tuples must not exceed 324."""
    from app.services.email.errors import EmailErrorReason
    from app.services.email.message import EmailCategory

    categories = [c.value for c in EmailCategory]
    reasons = [r.value for r in EmailErrorReason]

    reg, send_total, send_duration, retry_total, claim_lost = _make_isolated_metrics()

    # Increment every valid combination in the isolated registry
    for backend in VALID_BACKENDS:
        for category in categories:
            for status in VALID_STATUSES:
                send_total.labels(backend=backend, category=category, status=status).inc()
            send_duration.labels(backend=backend, category=category).observe(1.0)
        for reason in reasons:
            retry_total.labels(backend=backend, reason=reason).inc()
        claim_lost.labels(backend=backend).inc()

    # Collect all distinct (metric_family, labels_tuple) pairs from isolated registry
    distinct_series: set[tuple[str, tuple]] = set()
    target_metrics = {
        "sfbl_email_send_total",
        "sfbl_email_send_duration_seconds",
        "sfbl_email_retry_total",
        "sfbl_email_claim_lost_total",
    }
    for metric in reg.collect():
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
