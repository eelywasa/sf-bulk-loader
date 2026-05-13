import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright configuration for the sf-bulk-loader E2E suite.
 *
 * Three projects per D2 (locked in e2e-testing-spec.md):
 *   tier-1a  — org-free flows; runs on every PR
 *   tier-1b  — metadata-reading flows using captured describe fixtures; runs on every PR
 *   tier-2   — state-mutating flows against a real scratch org; nightly + release-tag gate
 *
 * Run from tests/e2e/:
 *   npm run e2e:1a   →  playwright test --project=tier-1a
 *   npm run e2e:1b   →  playwright test --project=tier-1b
 *   npm run e2e:2    →  playwright test --project=tier-2
 */

const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:5173";

export default defineConfig({
  // Spec root — all .spec.ts files are discovered automatically
  testDir: ".",

  // Only pick up files inside the app/ playwright subtree (per D13)
  testMatch: "app/playwright/**/*.spec.ts",

  // Fail fast on first failure when running locally; CI can set this to false
  // for full-report mode via PLAYWRIGHT_BAIL env var
  ...(process.env.PLAYWRIGHT_BAIL !== "0" ? { maxFailures: 3 } : {}),

  // Expect assertions timeout
  expect: {
    timeout: 10_000,
  },

  // Global timeout per test
  timeout: 60_000,

  // Reporters: list in CI, dot locally (overrideable via PLAYWRIGHT_REPORTER)
  reporter: process.env.CI
    ? [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]]
    : [["list"]],

  use: {
    baseURL: BASE_URL,
    // Capture traces on retry so failures are diagnosable
    trace: "on-first-retry",
    // Screenshot on failure
    screenshot: "only-on-failure",
  },

  projects: [
    {
      name: "tier-1a",
      // Org-free: file pane, plan list, RBAC nav, settings, login
      testMatch: "app/playwright/tier-1a/**/*.spec.ts",
      use: {
        ...devices["Desktop Chrome"],
      },
    },
    {
      name: "tier-1b",
      // Metadata-reading: mapping panel, object combobox, SOQL validator
      // Backend must be booted with SF_DESCRIBE_FIXTURES_DIR set (see D5)
      testMatch: "app/playwright/tier-1b/**/*.spec.ts",
      use: {
        ...devices["Desktop Chrome"],
      },
    },
    {
      name: "tier-2",
      // State-mutating: actual loads, query result writes, retry semantics
      // Requires E2E_SCRATCH_ORG to be set. Serial by default per D3 (locked).
      testMatch: "app/playwright/tier-2/**/*.spec.ts",
      fullyParallel: false,
      use: {
        ...devices["Desktop Chrome"],
      },
    },
    {
      name: "helpers-unit",
      // Helper-module unit tests (SFBL-342) — exercise the live Playwright
      // TestInfo API directly so the production code path is what's
      // asserted. No browser context needed; the test bodies don't open
      // pages. Project-level `testMatch` overrides the top-level filter
      // so .test.ts files outside `app/playwright/` get discovered.
      testMatch: "sf/playwright/helpers/__tests__/**/*.test.ts",
    },
  ],
});
