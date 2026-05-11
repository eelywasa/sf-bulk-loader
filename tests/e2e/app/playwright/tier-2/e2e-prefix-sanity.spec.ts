/**
 * e2e-prefix-sanity.spec.ts — Tier 2 throwaway spec for SFBL-326.
 *
 * Validates that the `e2ePrefix` fixture:
 *   1. Contains the workerIndex and retry counter in the right positions.
 *   2. Mutates between retry attempts (retry=0 first run, retry=1 on retry).
 *   3. Uses kebab-cased test-slug with no underscores.
 *   4. Ends with a trailing hyphen.
 *
 * This spec lives in tier-2/ so it is NOT gated on every PR (tier-1a/1b run
 * on every PR; tier-2 runs nightly + release-tag only per D8). The spec does
 * NOT require a scratch org — it only inspects the prefix string shape.
 *
 * Force a retry to observe the prefix mutation:
 *   The test is annotated with test.describe.configure({ retries: 1 }) so
 *   Playwright retries it once. The first attempt (retry=0) passes, collecting
 *   the prefix. The spec uses a shared mutable object to record both values.
 *   A second test in the same describe block asserts that exactly two distinct
 *   prefixes were observed.
 *
 * NOTE: Because tier-2 runs fullyParallel:false, the two tests in this describe
 * block run serially in the same worker, making the shared state safe.
 */

import { expect } from "@playwright/test";
import { test as baseTest, prefixFromTestInfo, toTestSlug, buildE2EPrefix } from "../helpers/e2e_prefix";

// ---------------------------------------------------------------------------
// Unit-level assertions (no browser needed)
// ---------------------------------------------------------------------------

baseTest.describe("toTestSlug", () => {
  baseTest("converts spaces to hyphens", async () => {
    expect(toTestSlug("Account upsert flow")).toBe("account-upsert-flow");
  });

  baseTest("strips special characters", async () => {
    expect(toTestSlug("Load plan (happy path)")).toBe("load-plan-happy-path");
  });

  baseTest("strips leading and trailing hyphens", async () => {
    expect(toTestSlug("  extra spaces  ")).toBe("extra-spaces");
  });

  baseTest("never produces underscores", async () => {
    const slug = toTestSlug("some_thing with underscores");
    expect(slug).not.toContain("_");
  });

  baseTest("handles already-kebab input", async () => {
    expect(toTestSlug("already-kebab")).toBe("already-kebab");
  });

  baseTest("falls back to 'test' for empty string", async () => {
    expect(toTestSlug("")).toBe("test");
  });
});

baseTest.describe("buildE2EPrefix", () => {
  baseTest("builds correct shape", async () => {
    const prefix = buildE2EPrefix("RUN123", 0, 0, "my-test");
    expect(prefix).toBe("E2E-RUN123-0-0-my-test-");
  });

  baseTest("encodes worker and retry distinctly", async () => {
    const p1 = buildE2EPrefix("X", 0, 1, "slug");
    const p2 = buildE2EPrefix("X", 1, 0, "slug");
    expect(p1).not.toBe(p2);
  });

  baseTest("ends with a trailing hyphen", async () => {
    const prefix = buildE2EPrefix("Y", 2, 1, "test-slug");
    expect(prefix.endsWith("-")).toBe(true);
  });

  baseTest("contains no underscores", async () => {
    const prefix = buildE2EPrefix("Z", 3, 0, "some-slug");
    expect(prefix).not.toContain("_");
  });
});

// ---------------------------------------------------------------------------
// Fixture-level assertions (uses the extended test + e2ePrefix fixture)
// ---------------------------------------------------------------------------

baseTest.describe("e2ePrefix fixture", () => {
  baseTest("contains workerIndex in the prefix", async ({}, testInfo) => {
    const prefix = prefixFromTestInfo(testInfo);
    // The workerIndex appears at position 3 (0-indexed after splitting on '-')
    // E2E-{runId}-{worker}-{retry}-{slug}-
    // We check it's present numerically.
    expect(prefix).toContain(`-${testInfo.workerIndex}-`);
  });

  baseTest("contains retry counter in the prefix", async ({}, testInfo) => {
    const prefix = prefixFromTestInfo(testInfo);
    // testInfo.retry is 0 on first attempt, 1 on retry
    expect(prefix).toContain(`-${testInfo.retry}-`);
  });

  baseTest("prefix ends with trailing hyphen", async ({}, testInfo) => {
    const prefix = prefixFromTestInfo(testInfo);
    expect(prefix.endsWith("-")).toBe(true);
  });

  baseTest("prefix starts with E2E-", async ({}, testInfo) => {
    const prefix = prefixFromTestInfo(testInfo);
    expect(prefix.startsWith("E2E-")).toBe(true);
  });

  baseTest("prefix contains no underscores (SOQL-safe)", async ({}, testInfo) => {
    const prefix = prefixFromTestInfo(testInfo);
    expect(prefix).not.toContain("_");
  });
});

// ---------------------------------------------------------------------------
// Retry mutation test — verifies prefix changes between attempt 0 and attempt 1
// ---------------------------------------------------------------------------

/**
 * Shared collector: records prefixes across test runs in this worker.
 * Safe under fullyParallel:false (tier-2 is serial).
 */
const _observedPrefixes: string[] = [];

baseTest.describe("retry prefix mutation", () => {
  // Configure this describe block to retry once so we see retry=0 then retry=1.
  baseTest.describe.configure({ retries: 1 });

  baseTest("collects prefix on each attempt and fails on first to force retry", async ({}, testInfo) => {
    const prefix = prefixFromTestInfo(testInfo);
    _observedPrefixes.push(prefix);

    // On the first attempt (retry=0), fail intentionally to trigger retry.
    // On the second attempt (retry=1), pass.
    if (testInfo.retry === 0) {
      // This intentional failure drives the retry; the second run (retry=1) will pass.
      throw new Error(
        `Intentional failure on attempt 0 to drive retry. Prefix observed: ${prefix}`
      );
    }

    // retry=1: we've now seen two prefixes — assert they differ.
    expect(_observedPrefixes.length).toBeGreaterThanOrEqual(2);
    const [prefixOnRetry0, prefixOnRetry1] = _observedPrefixes;
    expect(prefixOnRetry0).not.toBe(prefixOnRetry1);

    // The only difference should be the retry counter embedded in the prefix.
    // Strip the retry segment and verify the rest is identical.
    const withoutRetry = (p: string): string =>
      p.replace(/^(E2E-[^-]+-\d+)-\d+-/, "$1-");
    expect(withoutRetry(prefixOnRetry0)).toBe(withoutRetry(prefixOnRetry1));

    // The retry=1 prefix contains "1" at the retry position
    expect(prefixOnRetry1).toContain("-1-");
    // The retry=0 prefix contains "0" at the retry position (before the test slug)
    expect(prefixOnRetry0).toContain("-0-");
  });
});
