/**
 * Unit tests for the cross-layer Allure annotation helpers (SFBL-342).
 *
 * Locks the on-the-wire shape of every annotation each helper emits — if
 * the annotation `type` strings change here, every test in every tier
 * sees a different label in the Allure report. The Playwright reporter
 * (wired in SFBL-343 C) and the publish workflow (SFBL-345 E) both
 * depend on these exact strings, so behavioural changes belong in a
 * fresh story, not silent edits here.
 *
 * Runner: Playwright Test, in a dedicated `helpers-unit` project that has
 * no browser fixtures. The tests exercise the live `testInfo.annotations`
 * API used by the helpers, so the production code path and the test path
 * are identical.
 */

import { expect, test } from "@playwright/test";
import {
  JIRA_BASE_URL,
  labelLayer,
  labelTier,
  linkIssue,
  owner,
} from "../allure";

test.describe("linkIssue", () => {
  test("pushes an issue annotation with the full Jira URL", ({}, testInfo) => {
    const before = testInfo.annotations.length;
    linkIssue(testInfo, "SFBL-341");
    const added = testInfo.annotations.slice(before);
    expect(added).toEqual([
      { type: "issue", description: `${JIRA_BASE_URL}/SFBL-341` },
    ]);
  });

  test("rejects keys that aren't SFBL-\\d+", ({}, testInfo) => {
    expect(() => linkIssue(testInfo, "INVALID")).toThrow(/SFBL-/);
    expect(() => linkIssue(testInfo, "sfbl-1")).toThrow(/SFBL-/); // case sensitive
    expect(() => linkIssue(testInfo, "SFBL-")).toThrow(/SFBL-/);
    expect(() => linkIssue(testInfo, "")).toThrow(/SFBL-/);
  });
});

test.describe("labelTier", () => {
  for (const tier of ["1a", "1b", "2"] as const) {
    test(`pushes a tier label with value "${tier}"`, ({}, testInfo) => {
      const before = testInfo.annotations.length;
      labelTier(testInfo, tier);
      expect(testInfo.annotations.slice(before)).toEqual([
        { type: "tier", description: tier },
      ]);
    });
  }
});

test.describe("labelLayer", () => {
  for (const layer of ["e2e", "backend"] as const) {
    test(`pushes a layer label with value "${layer}"`, ({}, testInfo) => {
      const before = testInfo.annotations.length;
      labelLayer(testInfo, layer);
      expect(testInfo.annotations.slice(before)).toEqual([
        { type: "layer", description: layer },
      ]);
    });
  }
});

test.describe("owner", () => {
  test("pushes an owner annotation with the GitHub handle", ({}, testInfo) => {
    const before = testInfo.annotations.length;
    owner(testInfo, "eelywasa");
    expect(testInfo.annotations.slice(before)).toEqual([
      { type: "owner", description: "eelywasa" },
    ]);
  });
});

test.describe("composition", () => {
  test("applying all helpers preserves order in annotations", ({}, testInfo) => {
    const before = testInfo.annotations.length;
    labelTier(testInfo, "1a");
    labelLayer(testInfo, "e2e");
    linkIssue(testInfo, "SFBL-342");
    owner(testInfo, "eelywasa");
    expect(testInfo.annotations.slice(before)).toEqual([
      { type: "tier", description: "1a" },
      { type: "layer", description: "e2e" },
      { type: "issue", description: `${JIRA_BASE_URL}/SFBL-342` },
      { type: "owner", description: "eelywasa" },
    ]);
  });
});
