/**
 * Tier 1b canary — step editor object combobox fixture-loop (SFBL-322).
 *
 * What this proves:
 *   - The full Tier 1b fixture loop works end-to-end:
 *       frontend (StepEditorModal) → backend (/api/connections/{id}/objects)
 *       → fixture file (_object_list.json) → response → frontend render.
 *   - The object-name ComboInput in StepEditorModal is populated from the
 *     captured fixture when SF_DESCRIBE_FIXTURES_DIR is set.
 *   - Picking Account + upsert exposes the External ID field combobox,
 *     confirming the downstream UI branches on the selected object/operation.
 *
 * Backend fixture mode:
 *   The stack is booted with
 *     SF_DESCRIBE_FIXTURES_DIR=tests/e2e/app/fixtures/describe:tests/e2e/sf/fixtures/describe
 *   which causes /api/connections/{id}/objects to return the merged list from
 *   _object_list.json without touching Salesforce (SFBL-320).
 *
 * Test state setup (D10):
 *   1. A stub Salesforce connection record is created via the API at
 *      test-start so the plan editor has a connection_id to resolve.
 *      In fixture mode the backend ignores the credentials.
 *   2. A load plan linked to that connection is created via the API.
 *   Both are torn down in afterAll via the API.
 *
 * Auth mode handling:
 *   Same pattern as the Tier 1a canary — reads /api/runtime and only
 *   performs UI login when auth_mode !== "none".
 */

import { test, expect } from "@playwright/test";
import {
  fetchAuthMode,
  loginViaUi,
} from "../helpers/auth";
import {
  createConnection,
  deleteConnection,
  createPlan,
  deletePlan,
  type ConnectionRecord,
  type LoadPlanRecord,
} from "../helpers/api";
import { StepEditorModal } from "../helpers/pages/StepEditorModal";

// ── Constants ─────────────────────────────────────────────────────────────────

/**
 * Stub PEM accepted by the backend for fixture-mode tests.
 * The backend stores it encrypted but never uses it (no Salesforce call).
 */
const STUB_PRIVATE_KEY =
  "-----BEGIN RSA PRIVATE KEY-----\nFAKEKEY_SFBL322\n-----END RSA PRIVATE KEY-----";

/**
 * Representative subset of objects from tests/e2e/sf/fixtures/describe/_object_list.json.
 * Asserting all 2943 entries would be fragile and slow — pick five well-known
 * objects that should always appear in a standard Salesforce org fixture.
 */
const EXPECTED_OBJECTS = [
  "Account",
  "Contact",
  "User",
  "Lead",
  "Opportunity",
];

// ── Credential resolution ─────────────────────────────────────────────────────

const ADMIN_EMAIL =
  process.env.E2E_ADMIN_EMAIL ??
  process.env.ADMIN_EMAIL ??
  "admin@example.com";
const ADMIN_PASSWORD =
  process.env.E2E_ADMIN_PASSWORD ??
  process.env.ADMIN_PASSWORD ??
  "password";

// ── Suite ─────────────────────────────────────────────────────────────────────

test.describe("Step editor — object combobox fixture loop", () => {
  let connection: ConnectionRecord;
  let plan: LoadPlanRecord;

  // Create the connection + plan once for the whole suite.
  test.beforeAll(async ({ request }) => {
    connection = await createConnection(request, {
      name: "E2E Fixture Connection (SFBL-322)",
      instance_url: "https://fixture.salesforce.com",
      login_url: "https://login.salesforce.com",
      client_id: "e2e-fixture-client-id",
      private_key: STUB_PRIVATE_KEY,
      username: "fixture@example.com",
      is_sandbox: false,
    });

    plan = await createPlan(request, {
      name: "E2E Fixture Plan (SFBL-322)",
      connection_id: connection.id,
    });
  });

  // Tear down regardless of test outcome.
  test.afterAll(async ({ request }) => {
    if (plan?.id) {
      await deletePlan(request, plan.id);
    }
    if (connection?.id) {
      await deleteConnection(request, connection.id);
    }
  });

  test(
    "object combobox shows fixture options and Account upsert exposes External ID field",
    async ({ page, request }) => {
      // ── 1. Auth — mode-aware login ─────────────────────────────────────────
      const authMode = await fetchAuthMode(request);
      if (authMode !== "none") {
        await loginViaUi(page, ADMIN_EMAIL, ADMIN_PASSWORD);
      }

      // ── 2. Navigate to the plan editor ────────────────────────────────────
      await page.goto(`/plans/${plan.id}`);

      // Wait for the plan editor to finish loading (the step list or "save
      // first" banner should be visible — the plan already exists so we land
      // in edit mode immediately).
      await expect(
        page.getByRole("heading", { name: "Edit Load Plan" }),
      ).toBeVisible({ timeout: 15_000 });

      // ── 3. Open the Add Step modal ────────────────────────────────────────
      const modal = new StepEditorModal(page);
      await modal.openAddStep();

      // ── 4. Interact with the object combobox to reveal suggestions ────────
      //
      // ComboInput shows suggestions when the user focuses/types in the input.
      // Clicking the input and then clearing it (or typing a space that we
      // remove) reveals the full list.  We type nothing and just focus+wait
      // for the listbox to populate; if no suggestions appear on focus alone,
      // sending a single character that matches many objects is the fallback.
      //
      // Strategy: click the input to focus; the ComboInput renders the list
      // once the field has focus.  Wait up to 15 s for the fixture-backed
      // response (network round-trip to the Docker stack).
      await modal.objectInput.click();

      // Assert a representative subset of fixture objects is present.
      // These must come from _object_list.json via the fixture-mode backend.
      for (const objName of EXPECTED_OBJECTS) {
        await expect(modal.objectOption(objName)).toBeVisible({
          timeout: 15_000,
        });
      }

      // ── 5. Select Account and verify downstream UI ─────────────────────────
      await modal.selectObject("Account");

      // Confirm the combobox value now reads "Account"
      await expect(modal.objectInput).toHaveValue("Account");

      // Switch to upsert operation — this exposes the External ID field.
      await modal.selectOperation("upsert");

      // The External ID Field combobox must now be visible.  Its options are
      // populated from CSV column headers (pattern preview); since we haven't
      // supplied a CSV pattern the dropdown is empty but the combobox input
      // itself must render to confirm the upsert branch is active.
      await expect(modal.externalIdInput).toBeVisible({ timeout: 5_000 });

      // ── 6. Close without saving (teardown is via API in afterAll) ─────────
      await modal.cancel();
    },
  );
});
