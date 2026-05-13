/**
 * allure.ts — shared annotation helpers for the cross-layer test-evidence
 * dashboard (SFBL-334 / SFBL-342).
 *
 * The functions in this module push annotations onto a Playwright `TestInfo`
 * via the native `testInfo.annotations` API. `allure-playwright` (wired in
 * SFBL-343 C) reads those annotations at run-end and emits them as Allure
 * labels / links in the generated report.
 *
 * Until allure-playwright is wired, the annotations are inert but valid —
 * they appear in Playwright's own HTML report and JSON output and do not
 * affect test execution.
 *
 * The full taxonomy is documented at docs/specs/test-evidence-taxonomy.md.
 * Keep this file in sync if the contract changes there.
 *
 * Module placement: `tests/e2e/sf/playwright/helpers/` per the SFBL-316
 * convention — Salesforce-shaped or app-blind shared tooling lives here,
 * app-specific helpers live under `tests/e2e/app/playwright/helpers/`.
 */

import type { TestInfo } from "@playwright/test";

/** Base URL for SFBL Jira tickets. Combined with a key like "SFBL-341" to
 *  build the full https://… link the Allure `issue` link uses. */
export const JIRA_BASE_URL = "https://matthew-jenkin.atlassian.net/browse";

/** Valid `tier` label values per the taxonomy spec. Matches the Playwright
 *  project names (`tier-1a` / `tier-1b` / `tier-2`). */
export type Tier = "1a" | "1b" | "2";

/** Valid `layer` label values per the taxonomy spec. */
export type Layer = "e2e" | "backend";

/**
 * Attach a Jira-issue link to the current test case.
 *
 * The Allure reporter renders this as a clickable "issue" link in the test
 * card. Use this when the test was added in response to a specific Jira
 * ticket (regression coverage for a bug, acceptance criteria for a story,
 * etc.).
 *
 * @param testInfo  The current Playwright `TestInfo` (typically `test.info()`)
 * @param jiraKey   Bare key like "SFBL-341" — the helper builds the full URL
 *
 * @example
 *   import { test } from "@playwright/test";
 *   import { linkIssue } from "../sf/playwright/helpers/allure";
 *
 *   test("user can create a connection", async ({ page }) => {
 *     linkIssue(test.info(), "SFBL-313");
 *     // … rest of the test
 *   });
 */
export function linkIssue(testInfo: TestInfo, jiraKey: string): void {
  if (!/^SFBL-\d+$/.test(jiraKey)) {
    throw new Error(
      `linkIssue: expected a key like "SFBL-341", got ${JSON.stringify(jiraKey)}`,
    );
  }
  testInfo.annotations.push({
    type: "issue",
    description: `${JIRA_BASE_URL}/${jiraKey}`,
  });
}

/**
 * Tag the current test with a `tier` label per the taxonomy spec.
 *
 * The value must match the Playwright project the test runs under
 * (`tier-1a`, `tier-1b`, or `tier-2`). The Allure dashboard uses this label
 * to slice runs by tier, so the same Allure run can contain multiple tiers
 * and the operator can filter.
 *
 * @param testInfo  The current Playwright `TestInfo`
 * @param tier      "1a" | "1b" | "2"
 */
export function labelTier(testInfo: TestInfo, tier: Tier): void {
  testInfo.annotations.push({
    type: "tier",
    description: tier,
  });
}

/**
 * Tag the current test with a `layer` label per the taxonomy spec.
 *
 * For Playwright tests, the value is always "e2e". The pytest helper sets
 * "backend". This label lets the dashboard group cross-layer reports
 * cleanly when the same Allure run aggregates results from both suites.
 *
 * @param testInfo  The current Playwright `TestInfo`
 * @param layer     "e2e" | "backend"
 */
export function labelLayer(testInfo: TestInfo, layer: Layer): void {
  testInfo.annotations.push({
    type: "layer",
    description: layer,
  });
}

/**
 * Tag the current test with an `owner` label per the taxonomy spec.
 *
 * Allure recognises `owner` as a built-in label type and surfaces it in the
 * test card UI. The value should be a GitHub handle (e.g. "eelywasa"). The
 * label is optional — only use it on long-lived suites where the
 * "who's on pager" question matters.
 *
 * @param testInfo  The current Playwright `TestInfo`
 * @param ownerKey  GitHub handle of the test owner
 */
export function owner(testInfo: TestInfo, ownerKey: string): void {
  testInfo.annotations.push({
    type: "owner",
    description: ownerKey,
  });
}
