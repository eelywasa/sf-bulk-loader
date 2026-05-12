/**
 * Tier 2 canary spec — Account INSERT end-to-end (SFBL-328).
 *
 * What this proves:
 *   The full load path works against a real Salesforce scratch org:
 *     CSV in local input dir → bulk loader plan (created via API) →
 *     run triggered via UI → Bulk API 2.0 → 50 Account records exist
 *     in the org with the expected External_Id__c values.
 *
 * Spec ref: D2 (Tier 2), D3 (per-test-run prefix, hyphen-only), D10 (API setup),
 *           D12 (sfQuery assertion contract).
 *
 * Filesystem-seed pattern (same as SFBL-318, Tier 1a):
 *   There is no upload UI in the bulk loader — the Files page is browse/preview
 *   only.  The test harness writes the generated CSV directly into the
 *   backend's configured input directory (E2E_INPUT_DIR or ./data/input).
 *   The plan's csv_file_pattern references the seeded filename.
 *
 * CSV generation:
 *   tests/e2e/app/fixtures/csv/accounts_insert.py --prefix=${e2ePrefix}
 *   The script outputs a deterministic 50-row CSV with External_Id__c values
 *   of the form ${e2ePrefix}NNN.
 *
 * Cleanup:
 *   - wipe_test_records.py removes the 50 Account records from the org.
 *   - The seeded CSV is removed from the input directory.
 *   - The plan is deleted via the API.
 *   Cleanup runs in afterEach so it fires even on test failure.
 *
 * Prerequisites (set by the Tier 2 CI workflow):
 *   E2E_SCRATCH_ORG              — scratch org alias (for sf CLI calls)
 *   E2E_INPUT_DIR                — host-side path to the backend's input dir
 *   E2E_SCRATCH_ORG_INSTANCE_URL — for createScratchOrgConnection
 *   E2E_SCRATCH_ORG_LOGIN_URL    — for createScratchOrgConnection
 *   E2E_SCRATCH_ORG_USERNAME     — for createScratchOrgConnection
 *   E2E_BULK_LOADER_CONSUMER_KEY — for createScratchOrgConnection
 *   SFBL_E2E_BULK_LOADER_JWT_KEY — PEM private key (for createScratchOrgConnection)
 */

import * as child_process from "child_process";
import * as fs from "fs";
import * as path from "path";
import { expect } from "@playwright/test";
import { test as baseTest } from "../helpers/e2e_prefix";
import { fetchAuthMode, loginViaUi } from "../helpers/auth";
import {
  createScratchOrgConnection,
  deleteScratchOrgConnection,
} from "../helpers/setup_connection";
import { createPlan, createStep, deletePlan } from "../helpers/api";
import type { ConnectionRecord, LoadPlanRecord } from "../helpers/api";
import { sfQuery } from "../../../sf/playwright/helpers/sf-helper";

// ── Constants ─────────────────────────────────────────────────────────────────

const ACCOUNT_COUNT = 50;
/** Timeout for a real load run against a scratch org (in ms). */
const RUN_COMPLETION_TIMEOUT_MS = 5 * 60 * 1000; // 5 minutes
/** Polling interval when waiting for a run to reach a terminal status (ms). */
const RUN_POLL_INTERVAL_MS = 5_000;

// ── Input directory resolution ─────────────────────────────────────────────────

/**
 * Resolve the host-side input directory.
 * See the equivalent in files-pane.spec.ts (Tier 1a) — same pattern.
 */
function resolveInputDir(): string {
  if (process.env["E2E_INPUT_DIR"]) {
    return process.env["E2E_INPUT_DIR"];
  }
  // Default: ./data/input relative to repo root.
  // From tier-2/account-insert.spec.ts, the repo root is five levels up:
  //   tier-2 → playwright → app → e2e → tests → repo root
  // (Note: "tests/e2e" is two directories, not one — an earlier six-level
  // traversal here pointed at the parent of the repo and caused the backend
  // to never see the seeded CSV, so the run completed with 0 jobs.)
  const repoRoot = path.resolve(__dirname, "../../../../..");
  return path.join(repoRoot, "data", "input");
}

// ── CSV generation via the Python script ──────────────────────────────────────

/**
 * Call accounts_insert.py to generate the CSV and return its content as a
 * string.  The script writes to --out; we read it back.
 *
 * @param prefix   The per-test-run e2ePrefix.
 * @param outPath  Where to write the CSV (temporary path, cleaned up by caller).
 */
function generateAccountsCsv(prefix: string, outPath: string): string {
  const scriptPath = path.resolve(
    __dirname,
    "../../fixtures/csv/accounts_insert.py",
  );
  child_process.execSync(
    `python3 ${shellQuote(scriptPath)} --prefix ${shellQuote(prefix)} --out ${shellQuote(outPath)}`,
    { stdio: ["pipe", "pipe", "pipe"] },
  );
  return fs.readFileSync(outPath, "utf8");
}

function shellQuote(s: string): string {
  return `'${s.replace(/'/g, "'\\''")}'`;
}

// ── Terminal status check ─────────────────────────────────────────────────────

const TERMINAL_STATUSES = new Set([
  "completed",
  "completed_with_errors",
  "failed",
  "aborted",
]);

// ── Extended test fixture ─────────────────────────────────────────────────────

const test = baseTest;

// ── Tests ─────────────────────────────────────────────────────────────────────

test.describe("Tier 2 — Account INSERT end-to-end", () => {
  // Mutable state shared within the describe block.
  // Because tier-2 runs fullyParallel:false, beforeEach/afterEach are safe.
  let connection: ConnectionRecord | undefined;
  let plan: LoadPlanRecord | undefined;
  let seedCsvPath: string | undefined;

  const inputDir = resolveInputDir();

  const adminEmail =
    process.env["E2E_ADMIN_EMAIL"] ??
    process.env["ADMIN_EMAIL"] ??
    "admin@example.com";
  const adminPassword =
    process.env["E2E_ADMIN_PASSWORD"] ??
    process.env["ADMIN_PASSWORD"] ??
    "password";

  test.beforeEach(async ({ request, e2ePrefix }) => {
    // 1. Create a bulk-loader Connection pointing at the scratch org.
    connection = await createScratchOrgConnection(request);

    // 2. Generate the accounts CSV in a temp location, then seed to input dir.
    fs.mkdirSync(inputDir, { recursive: true });
    const csvFilename = `accounts-insert-${e2ePrefix.replace(/[^a-z0-9-]/gi, "-")}.csv`;
    seedCsvPath = path.join(inputDir, csvFilename);

    const tmpOut = path.join(inputDir, `_tmp_${csvFilename}`);
    generateAccountsCsv(e2ePrefix, tmpOut);
    // Rename to final name (atomic on same filesystem).
    fs.renameSync(tmpOut, seedCsvPath);

    // 3. Create a load plan + step via the API (D10).
    plan = await createPlan(request, {
      name: `E2E Account Insert — ${e2ePrefix}`,
      connection_id: connection.id,
    });
    await createStep(request, plan.id, {
      object_name: "Account",
      operation: "insert",
      csv_file_pattern: csvFilename,
      // Use default partition size — 10,000 rows means the 50-row CSV is one job.
      partition_size: 10_000,
    });
  });

  test.afterEach(async ({ request, e2ePrefix }) => {
    // Cleanup: Salesforce records → seeded CSV → plan.
    // Each step is wrapped independently so partial failures don't skip later steps.

    // 1. Wipe records from the scratch org.
    try {
      const scriptPath = path.resolve(
        __dirname,
        "../../../../sf/scripts/wipe_test_records.py",
      );
      const org = process.env["E2E_SCRATCH_ORG"] ?? "";
      if (org) {
        child_process.execSync(
          `python3 ${shellQuote(scriptPath)} --prefix ${shellQuote(e2ePrefix)} --org ${shellQuote(org)}`,
          { stdio: "pipe" },
        );
      }
    } catch {
      // Cleanup failure should not mask the test result.
    }

    // 2. Remove seeded CSV.
    if (seedCsvPath) {
      try {
        fs.unlinkSync(seedCsvPath);
      } catch {
        // File may have already been cleaned up.
      }
      seedCsvPath = undefined;
    }

    // 3. Delete the plan (cascades to steps and runs via backend FK logic).
    if (plan) {
      try {
        await deletePlan(request, plan.id);
      } catch {
        // Already deleted or plan creation failed — ignore.
      }
      plan = undefined;
    }

    // 4. Delete the connection.
    if (connection) {
      try {
        await deleteScratchOrgConnection(request, connection.id);
      } catch {
        // Already deleted or connection creation failed — ignore.
      }
      connection = undefined;
    }
  });

  test(
    "50 Accounts inserted with correct External_Id__c values",
    { tag: "@tier-2" },
    async ({ page, request, e2ePrefix }) => {
      // Guard: connection and plan must exist (beforeEach succeeded).
      if (!connection || !plan) {
        throw new Error("beforeEach did not complete — connection or plan is undefined");
      }

      // ── Auth ────────────────────────────────────────────────────────────────
      const authMode = await fetchAuthMode(request);
      if (authMode !== "none") {
        await loginViaUi(page, adminEmail, adminPassword);
      }

      // ── Navigate to the plan ─────────────────────────────────────────────────
      await page.goto(`/plans/${plan.id}`);
      // Wait for the plan page to load (plan name should be visible).
      await expect(
        page.getByText(plan.name, { exact: false }),
      ).toBeVisible({ timeout: 15_000 });

      // ── Trigger the run via the UI's "Start Run" button ──────────────────────
      await page.getByRole("button", { name: "Start Run" }).click();

      // Wait for a redirect to a run detail page (/runs/<id>).
      await page.waitForURL(/\/runs\/[^/]+$/, { timeout: 30_000 });
      const runUrl = page.url();
      const runId = runUrl.split("/").pop();
      if (!runId) throw new Error(`Could not extract run ID from URL: ${runUrl}`);

      // ── Poll the API until the run reaches a terminal status ─────────────────
      const deadline = Date.now() + RUN_COMPLETION_TIMEOUT_MS;
      let finalStatus: string | undefined;

      while (Date.now() < deadline) {
        const resp = await request.get(`/api/runs/${runId}`);
        if (!resp.ok()) {
          throw new Error(`GET /api/runs/${runId} failed: ${resp.status()}`);
        }
        const body = (await resp.json()) as { status: string };
        if (TERMINAL_STATUSES.has(body.status)) {
          finalStatus = body.status;
          break;
        }
        await new Promise((r) => setTimeout(r, RUN_POLL_INTERVAL_MS));
      }

      if (!finalStatus) {
        throw new Error(
          `Run ${runId} did not reach a terminal status within ${RUN_COMPLETION_TIMEOUT_MS / 1000}s`,
        );
      }

      // ── Assert run completed (not failed/aborted) ────────────────────────────
      expect(
        finalStatus,
        `Expected run to complete successfully but got status: ${finalStatus}`,
      ).toMatch(/^completed/);

      // ── Assert 50 Account records exist in the org with expected prefix ───────
      const rows = sfQuery(
        `SELECT Id, External_Id__c FROM Account WHERE External_Id__c LIKE '${e2ePrefix}%'`,
      );
      expect(
        rows.length,
        `Expected ${ACCOUNT_COUNT} Accounts but sfQuery returned ${rows.length}`,
      ).toBe(ACCOUNT_COUNT);
    },
  );
});
