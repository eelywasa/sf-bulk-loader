"""Shared annotation helpers for the cross-layer test-evidence dashboard.

SFBL-334 / SFBL-342. Python mirror of `tests/e2e/sf/playwright/helpers/allure.ts`.

The functions in this module are decorator factories — they wrap
``allure.label`` / ``allure.issue`` so callers can write taxonomy-conformant
annotations without remembering label-name strings. Applied at module
level via ``pytestmark`` or per-test via stacked decorators::

    from backend.tests._allure_helpers import (
        label_layer, label_tier, link_issue
    )

    pytestmark = [label_layer("backend"), label_tier("1a")]

    @link_issue("SFBL-341")
    def test_evidence_stack_deploys() -> None:
        ...

Until ``allure-pytest`` is wired into the CI pytest invocation (SFBL-344 D),
these decorators register metadata that nothing reads — they are inert but
correct. After D, every test author gets a consistent label vocabulary in
the Allure report.

The full taxonomy is documented at ``docs/specs/test-evidence-taxonomy.md``.
Keep this file in sync if the contract changes there.
"""

from __future__ import annotations

from typing import Literal

import allure

# Base URL for SFBL Jira tickets. Combined with a key like "SFBL-341" to
# build the full https://… link the Allure ``issue`` link uses.
JIRA_BASE_URL = "https://matthew-jenkin.atlassian.net/browse"

# Valid ``tier`` label values per the taxonomy spec. Matches the Playwright
# project names. Backend tests are always Tier 1a today, but the type
# allows the full set for forward-compat.
Tier = Literal["1a", "1b", "2"]
Layer = Literal["e2e", "backend"]


def link_issue(jira_key: str):
    """Attach a Jira-issue link to the decorated test.

    The Allure reporter renders this as a clickable "issue" link in the test
    card. Use this when the test was added in response to a specific Jira
    ticket (regression coverage for a bug, acceptance criteria for a story).

    :param jira_key: Bare key like ``"SFBL-341"`` — the helper builds the
        full URL from :data:`JIRA_BASE_URL`. Format is enforced.
    """
    import re

    if not re.fullmatch(r"SFBL-\d+", jira_key):
        raise ValueError(
            f"link_issue: expected a key like 'SFBL-341', got {jira_key!r}"
        )
    return allure.issue(f"{JIRA_BASE_URL}/{jira_key}", name=jira_key)


def label_tier(tier: Tier):
    """Tag the decorated test with a ``tier`` label per the taxonomy spec.

    For Playwright the value mirrors the project name; for backend pytest
    it's always ``"1a"`` today (backend tests are not cross-tier yet).
    """
    return allure.label("tier", tier)


def label_layer(layer: Layer):
    """Tag the decorated test with a ``layer`` label per the taxonomy spec.

    Backend pytest always passes ``"backend"``; ``"e2e"`` is reserved for
    the Playwright helper and shouldn't appear in this module's callers.
    """
    return allure.label("layer", layer)


def owner(owner_key: str):
    """Tag the decorated test with an ``owner`` label.

    Allure recognises ``owner`` as a built-in label type and surfaces it in
    the test card UI. The value should be a GitHub handle. Optional but
    recommended on long-lived modules where "who's on pager" matters.
    """
    return allure.label("owner", owner_key)
