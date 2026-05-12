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

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
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
baseTest.describe("retry prefix mutation", () => {
  // Configure this describe block to retry once so we see retry=0 then retry=1.
  baseTest.describe.configure({ retries: 1 });

  // Cross-attempt state via filesystem: Playwright re-imports the spec module
  // on retry, so module-level variables don't survive.  We persist the
  // observed prefix from attempt 0 to a tempfile keyed by the test's title
  // hash, then read it back on attempt 1 to verify the prefix mutated as
  // expected (only the retry segment should have changed).
  const stateFile = (title: string): string => {
    const hash = title.replace(/[^A-Za-z0-9]/g, "-").slice(0, 60);
    return path.join(os.tmpdir(), `e2e-prefix-retry-${hash}.txt`);
  };

  baseTest("captures prefix across forced retry and asserts only the retry segment mutates", async ({}, testInfo) => {
    const prefix = prefixFromTestInfo(testInfo);
    const file = stateFile(testInfo.title);

    if (testInfo.retry === 0) {
      // Persist the attempt-0 prefix for the retry to read, then force a fail.
      fs.writeFileSync(file, prefix);
      throw new Error(
        `Intentional failure on attempt 0 to drive retry. Prefix observed: ${prefix}`
      );
    }

    // retry=1: read the attempt-0 prefix from disk and compare to the
    // attempt-1 prefix.
    expect(fs.existsSync(file), `Expected attempt-0 state file at ${file}`).toBe(true);
    const prefixOnRetry0 = fs.readFileSync(file, "utf8");
    const prefixOnRetry1 = prefix;

    // Clean up the state file regardless of assertion outcome.
    try { fs.unlinkSync(file); } catch { /* best-effort */ }

    // Both prefixes must encode their respective retry segment correctly.
    // Match against the segment shape ("...-<worker>-0-..." vs "...-<worker>-1-...")
    // rather than substring to avoid false matches with other digits.
    //
    // NOTE: workerIndex CAN differ between attempts — Playwright doesn't
    // guarantee retried tests run on the same worker.  Asserting that the
    // workerIndex segment is identical across attempts is incorrect (PR #89
    // run 25685808038 observed worker 2 → worker 4 between attempts).  The
    // contract we actually want to prove is "the retry counter is encoded
    // and increments", not "everything but retry is identical".
    expect(prefixOnRetry0).toMatch(/^E2E-[^-]+-\d+-0-/);
    expect(prefixOnRetry1).toMatch(/^E2E-[^-]+-\d+-1-/);

    // The RUN_ID and test-slug segments must be identical across attempts
    // (those are deterministic functions of the test+workflow, not the worker).
    const runIdAndSlug = (p: string): string =>
      p.replace(/^(E2E-[^-]+)-\d+-\d+-(.*)$/, "$1::$2");
    expect(runIdAndSlug(prefixOnRetry0)).toBe(runIdAndSlug(prefixOnRetry1));
  });
});
