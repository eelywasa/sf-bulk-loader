/**
 * Tier 1a canary — file pane filesystem-seed flow (SFBL-318).
 *
 * What this proves:
 *   - The Playwright scaffold reaches a running stack.
 *   - Login via the UI works end-to-end.
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
 * Login uses the UI (a Tier 1a-eligible flow in its own right). Credentials
 * are read from E2E_ADMIN_EMAIL / E2E_ADMIN_PASSWORD, falling back to the
 * docker-compose dev defaults seeded by ADMIN_EMAIL / ADMIN_PASSWORD.
 */

import * as fs from "fs";
import * as path from "path";
import { test, expect } from "@playwright/test";
import { FilesPage } from "../helpers/pages/FilesPage";

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

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Log in via the UI login form.
 * Returns after the browser has navigated away from /login.
 */
async function loginViaUi(
  page: import("@playwright/test").Page,
  email: string,
  password: string,
): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByTestId("login-submit").click();
  // Wait until we leave /login (redirect to the requested page)
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), {
    timeout: 15_000,
  });
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

  test.beforeEach(async () => {
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
  }) => {
    // 1. Log in via the UI
    await loginViaUi(page, email, password);

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
