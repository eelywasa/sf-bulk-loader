"""Unit tests for the cross-layer Allure annotation helpers (SFBL-342).

Locks the on-the-wire arguments each helper passes through to
``allure-pytest``. If the label-type strings or URL shape change here, the
Allure report's facets shift accordingly — behavioural changes belong in a
fresh story, not silent edits.

Strategy: monkeypatch the ``allure.label`` and ``allure.issue`` functions
inside the helper module so the test captures exactly what the helper would
have forwarded. We don't try to verify the live allure-pytest plugin
internals (those are allure's responsibility); we just verify our wrappers
build the right calls.
"""

from __future__ import annotations

import pytest

from tests import _allure_helpers as helpers
from tests._allure_helpers import JIRA_BASE_URL


def _capture_label(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Patch ``helpers.allure.label`` to capture ``(args, kwargs)`` calls.

    Returns the capture list — appended to on every call. The decorator
    returned is an identity function so any decorator-style usage still
    composes cleanly.
    """
    captured: list[tuple] = []

    def fake_label(*args, **kwargs):
        captured.append((args, kwargs))
        return lambda func: func

    monkeypatch.setattr(helpers.allure, "label", fake_label)
    return captured


def _capture_issue(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    captured: list[tuple] = []

    def fake_issue(*args, **kwargs):
        captured.append((args, kwargs))
        return lambda func: func

    monkeypatch.setattr(helpers.allure, "issue", fake_issue)
    return captured


# ──────────────────────────────────────────────────────────────────────────
# link_issue
# ──────────────────────────────────────────────────────────────────────────


def test_link_issue_calls_allure_issue_with_full_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_issue(monkeypatch)
    helpers.link_issue("SFBL-341")
    assert captured == [
        ((f"{JIRA_BASE_URL}/SFBL-341",), {"name": "SFBL-341"}),
    ]


@pytest.mark.parametrize(
    "bad_key", ["INVALID", "sfbl-1", "SFBL-", "", "SFBL-1a", "SFBL_1", "FOO-1"]
)
def test_link_issue_rejects_non_sfbl_keys(bad_key: str) -> None:
    with pytest.raises(ValueError, match="SFBL-"):
        helpers.link_issue(bad_key)


# ──────────────────────────────────────────────────────────────────────────
# label_tier
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tier", ["1a", "1b", "2"])
def test_label_tier_calls_allure_label_with_tier(
    monkeypatch: pytest.MonkeyPatch, tier: str
) -> None:
    captured = _capture_label(monkeypatch)
    helpers.label_tier(tier)  # type: ignore[arg-type]
    assert captured == [(("tier", tier), {})]


# ──────────────────────────────────────────────────────────────────────────
# label_layer
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("layer", ["e2e", "backend"])
def test_label_layer_calls_allure_label_with_layer(
    monkeypatch: pytest.MonkeyPatch, layer: str
) -> None:
    captured = _capture_label(monkeypatch)
    helpers.label_layer(layer)  # type: ignore[arg-type]
    assert captured == [(("layer", layer), {})]


# ──────────────────────────────────────────────────────────────────────────
# owner
# ──────────────────────────────────────────────────────────────────────────


def test_owner_calls_allure_label_with_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_label(monkeypatch)
    helpers.owner("eelywasa")
    assert captured == [(("owner", "eelywasa"), {})]


# ──────────────────────────────────────────────────────────────────────────
# composition — ordering preserved across helpers
# ──────────────────────────────────────────────────────────────────────────


def test_helpers_applied_in_order_record_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    label_calls = _capture_label(monkeypatch)
    issue_calls = _capture_issue(monkeypatch)

    helpers.label_tier("1a")
    helpers.label_layer("backend")
    helpers.link_issue("SFBL-342")
    helpers.owner("eelywasa")

    assert label_calls == [
        (("tier", "1a"), {}),
        (("layer", "backend"), {}),
        (("owner", "eelywasa"), {}),
    ]
    assert issue_calls == [
        ((f"{JIRA_BASE_URL}/SFBL-342",), {"name": "SFBL-342"}),
    ]
