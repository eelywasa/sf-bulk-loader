/**
 * Tier 1a canary — file pane filesystem-seed flow (SFBL-318).
 *
 * What this proves:
 *   - The Playwright scaffold reaches a running stack.
 *   - A CSV seeded directly into the backend's input directory appears in the
 *     Files page list (no upload UI exists; the Files page is browse-only).
 *   - Selecting the file opens a preview pane rendering the expected header row.
 *
 * Filesystem-seed pattern (D10): the test harness writes a small CSV into the
 * configured local input directory before each test and removes it afterwards.
 * The directory is resolved from:
 *   1. E2E_INPUT_DIR env var (set by docker-compose / CI harness).
 *   2. ./data/input relative to the repo root (default docker-compose mount).
 *
 * Auth mode handling — the spec is mode-aware:
 *   - In `auth_mode=none` (CI's `APP_DISTRIBUTION=desktop` profile), there is
 *     no login wall and no admin user is seeded; the spec navigates directly
 *     to /files.
 *   - In `auth_mode=local`, the spec performs UI login first. Credentials are
 *     read from E2E_ADMIN_EMAIL / E2E_ADMIN_PASSWORD, falling back to the
 *     stack's seeded ADMIN_EMAIL / ADMIN_PASSWORD env vars.
 *   - Mode is detected at test time via the unauthenticated `/api/runtime`
 *     endpoint, so the same spec runs in both configurations.
 */

import * as fs from "fs";
import * as path from "path";
import { test, expect } from "@playwright/test";
import { FilesPage } from "../helpers/pages/FilesPage";
import { fetchAuthMode, loginViaUi } from "../helpers/auth";
import {
  labelLayer,
  labelTier,
  linkIssue,
} from "../../../sf/playwright/helpers/allure";

// ── Seed CSV definition ───────────────────────────────────────────────────────

const SEED_FILENAME = "e2e-canary-seed.csv";

/** Header row used in the seeded CSV. */
const SEED_HEADER = ["Id", "Name", "Email__c"];

/** Two data rows — only the header row is asserted in the preview check. */
const SEED_ROWS = [
  ["001000000000001AAA", "Alice Canary", "alice@example.com"],
  ["001000000000002AAA", "Bob Canary", "bob@example.com"],
];

function buildCsvContent(): string {
  const rows = [SEED_HEADER, ...SEED_ROWS];
  return rows.map((r) => r.join(",")).join("\n") + "\n";
}

// ── Input directory resolution ────────────────────────────────────────────────

/**
 * Resolve the host-side input directory.
 *
 * In docker-compose the container path is /data/input, which is volume-mounted
 * from ./data/input in the repo root. We need the *host-side* path so we can
 * write there from the test process (which runs on the host, not in the
 * container). E2E_INPUT_DIR must be set when the stack is running in Docker;
 * it can be omitted when the backend is run with input_dir pointing to a
 * writable path on the host directly.
 */
function resolveInputDir(): string {
  if (process.env.E2E_INPUT_DIR) {
    return process.env.E2E_INPUT_DIR;
  }
  // Default: ./data/input relative to repo root, three levels up from
  // tests/e2e/app/playwright/tier-1a/ → tests/e2e/ → tests/ → repo root
  const repoRoot = path.resolve(__dirname, "../../../../..");
  return path.join(repoRoot, "data", "input");
}

// ── Tests ─────────────────────────────────────────────────────────────────────

test.describe("Files pane — filesystem-seed flow", () => {
  const inputDir = resolveInputDir();
  const seedPath = path.join(inputDir, SEED_FILENAME);

  const email =
    process.env.E2E_ADMIN_EMAIL ??
    process.env.ADMIN_EMAIL ??
    "admin@example.com";
  const password =
    process.env.E2E_ADMIN_PASSWORD ??
    process.env.ADMIN_PASSWORD ??
    "password";

  test.beforeEach(async ({}, testInfo) => {
    // SFBL-334 Allure annotations — taxonomy: tier=1a, layer=e2e, issue=SFBL-318.
    linkIssue(testInfo, "SFBL-318");
    labelTier(testInfo, "1a");
    labelLayer(testInfo, "e2e");

    // Ensure the input directory exists (no-op if already present)
    fs.mkdirSync(inputDir, { recursive: true });
    // Write the seed CSV
    fs.writeFileSync(seedPath, buildCsvContent(), "utf8");
  });

  test.afterEach(async () => {
    // Always clean up, even on test failure
    try {
      fs.unlinkSync(seedPath);
    } catch {
      // If the file doesn't exist, no problem
    }
  });

  test("seeded CSV appears in the file list and preview renders header row", async ({
    page,
    request,
  }) => {
    // 1. Discover the stack's auth mode and only log in when one is enforced.
    //    In CI the stack runs with APP_DISTRIBUTION=desktop → auth_mode=none,
    //    which skips admin seeding and has no login wall; calling the login
    //    form there would post fallback credentials that don't exist in the
    //    fresh desktop DB and time out the wait. Mode is detected dynamically
    //    so the same spec also works against an auth-enabled stack (where
    //    the operator has seeded a fixture admin user via ADMIN_EMAIL /
    //    ADMIN_PASSWORD).
    const authMode = await fetchAuthMode(request);
    if (authMode !== "none") {
      await loginViaUi(page, email, password);
    }

    // 2. Navigate to /files
    const filesPage = new FilesPage(page);
    await filesPage.goto();

    // 3. Assert the seeded file appears in the list
    await expect(filesPage.fileEntry(SEED_FILENAME)).toBeVisible({
      timeout: 10_000,
    });

    // 4. Open the preview pane for that file
    await filesPage.selectFile(SEED_FILENAME);

    // 5. Assert the preview panel renders with the expected header columns
    for (const col of SEED_HEADER) {
      await expect(filesPage.previewColumnHeader(col)).toBeVisible({
        timeout: 10_000,
      });
    }
  });
});
