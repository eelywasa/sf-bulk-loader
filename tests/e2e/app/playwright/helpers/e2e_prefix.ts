/**
 * e2e_prefix.ts — Playwright fixture that builds a per-test-run unique prefix.
 *
 * Spec ref: D3 (locked). The prefix shape is:
 *
 *   E2E-{RUN_ID}-{WORKER}-{RETRY}-{TEST_SLUG}-
 *
 * Components:
 *   RUN_ID   — process.env.E2E_RUN_ID if set (populated by CI from github.run_id);
 *              falls back to a short timestamp-based value for local dev runs.
 *   WORKER   — testInfo.workerIndex (Playwright's per-worker index on the TestInfo
 *              object — NOT process.env.TEST_WORKER_INDEX which does not exist).
 *   RETRY    — testInfo.retry (Playwright's per-retry counter, 0-indexed —
 *              NOT process.env.TEST_RETRY_COUNT which does not exist).
 *   TEST_SLUG — kebab-cased test title, alpha/digit/hyphen only (no underscores).
 *
 * Why hyphens:
 *   Underscore is a SOQL LIKE single-character wildcard. An underscore-built prefix
 *   produces false-match cleanup queries. Hyphens are SOQL-safe. The trailing
 *   hyphen disambiguates E2E-...-foo- from E2E-...-foo-bar- at prefix-match time.
 *
 * Usage in a spec:
 *
 *   import { test } from "@playwright/test";
 *   import { e2ePrefixFixtures } from "../helpers/e2e_prefix";
 *
 *   const { test: testWithPrefix } = test.extend(e2ePrefixFixtures);
 *
 *   testWithPrefix("my scenario", async ({ e2ePrefix }) => {
 *     console.log(e2ePrefix); // "E2E-12345-0-0-my-scenario-"
 *   });
 */

import { test as base, TestInfo } from "@playwright/test";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Derive a short RUN_ID for local dev when E2E_RUN_ID is not set by CI.
 * Uses the seconds since Unix epoch, which is short and sortable.
 */
function localRunId(): string {
  return String(Math.floor(Date.now() / 1000));
}

/**
 * Convert a test title to a kebab-case slug suitable as a SOQL-safe prefix
 * component.
 *
 * Rules (per D3):
 *   - Lowercase.
 *   - Replace any run of non-alphanumeric characters with a single hyphen.
 *   - Strip leading/trailing hyphens.
 *   - Never produce underscores (SOQL wildcard).
 *
 * Examples:
 *   "Account upsert flow"  → "account-upsert-flow"
 *   "Load plan (happy)"    → "load-plan-happy"
 *   "   extra spaces   "   → "extra-spaces"
 */
export function toTestSlug(title: string): string {
  return title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    || "test";
}

/**
 * Build the canonical E2E prefix from its component parts.
 *
 * The returned string always ends with a hyphen so it can be used directly
 * as a SOQL LIKE pattern prefix: `LIKE '${e2ePrefix}%'`.
 *
 * @param runId       - CI run identifier (github.run_id or local timestamp).
 * @param workerIndex - testInfo.workerIndex.
 * @param retry       - testInfo.retry.
 * @param testSlug    - kebab-cased test title.
 * @returns prefix string, e.g. "E2E-12345-0-0-account-upsert-flow-"
 */
export function buildE2EPrefix(
  runId: string,
  workerIndex: number,
  retry: number,
  testSlug: string,
): string {
  return `E2E-${runId}-${workerIndex}-${retry}-${testSlug}-`;
}

/**
 * Derive the full e2ePrefix from a Playwright TestInfo object.
 *
 * This is the canonical single-call API used by fixture implementations.
 * It reads workerIndex and retry directly from testInfo (per D3 — these are
 * NOT available as process.env variables).
 */
export function prefixFromTestInfo(testInfo: TestInfo): string {
  const runId = process.env["E2E_RUN_ID"] ?? localRunId();
  const slug = toTestSlug(testInfo.title);
  return buildE2EPrefix(runId, testInfo.workerIndex, testInfo.retry, slug);
}

// ---------------------------------------------------------------------------
// Fixture type
// ---------------------------------------------------------------------------

export interface E2EPrefixFixtures {
  /**
   * A per-test-run unique prefix in the shape:
   *   E2E-{RUN_ID}-{WORKER}-{RETRY}-{TEST_SLUG}-
   *
   * Safe to use directly in SOQL LIKE queries. Never contains underscores.
   */
  e2ePrefix: string;
}

// ---------------------------------------------------------------------------
// Fixture export
// ---------------------------------------------------------------------------

/**
 * Playwright fixture object to extend `test` with `e2ePrefix`.
 *
 * ```ts
 * import { test as base } from "@playwright/test";
 * import { e2ePrefixFixtures } from "./e2e_prefix";
 *
 * export const test = base.extend<E2EPrefixFixtures>(e2ePrefixFixtures);
 * ```
 */
export const e2ePrefixFixtures: Parameters<typeof base.extend<E2EPrefixFixtures>>[0] = {
  e2ePrefix: async ({}, use, testInfo) => {
    const prefix = prefixFromTestInfo(testInfo);
    await use(prefix);
  },
};

/**
 * Pre-extended test object.  Import this instead of `@playwright/test`'s `test`
 * in any spec that needs the `e2ePrefix` fixture.
 *
 * ```ts
 * import { test, expect } from "../helpers/e2e_prefix";
 *
 * test("my scenario", async ({ e2ePrefix }) => { ... });
 * ```
 */
export const test = base.extend<E2EPrefixFixtures>(e2ePrefixFixtures);
export { expect } from "@playwright/test";
