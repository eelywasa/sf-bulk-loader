/**
 * Tier 1a — the plan editor refuses a step with no Salesforce Object (SFBL-403).
 *
 * What this proves:
 *   - Clearing the Salesforce Object field and saving surfaces an inline
 *     validation error in the step modal, and no update is sent.
 *   - The backend refuses the same value independently, so the guard is not
 *     only client-side.
 *   - A padded value is persisted trimmed.
 *
 * Why it matters: an empty `object_name` satisfies the model's `nullable=False`
 * column and, before this ticket, every schema check — the step then failed
 * only once a run reached Bulk API job creation, long after the operator had
 * left the editor. The incident plan behind SFBL-400 carried exactly such a row.
 *
 * Org-free by construction (Tier 1a): the connection carries a syntactically
 * valid but fake private key. The object combobox will show no suggestions
 * because it cannot describe the org, which is fine — this spec types free text
 * and never asserts on the suggestion list.
 *
 * COVERAGE LIMIT — read before extending. This covers the *forward* direction:
 * new empty values are refused. It does not cover the legacy row (stored
 * `object_name == ""`), because such a row can no longer be created through any
 * API this spec can reach — which is the point of the fix. The legacy-row
 * behaviours are covered where a row can be injected directly:
 *   - backend/tests/test_load_step_object_name.py  (GET stays 200, PATCH 422,
 *     duplication refused, startup log names the step)
 *   - frontend PlanEditor.test.tsx  ("No object set" badge, pre-seeded modal
 *     error, repair-and-save)
 */

import { test, expect } from "@playwright/test";
import { fetchAuthMode, loginViaUi } from "../helpers/auth";
import {
  createConnection,
  createPlan,
  createStep,
  defaultAdminCredentials,
  deleteConnection,
  deletePlan,
  loginViaApi,
} from "../helpers/api";
import { prefixFromTestInfo } from "../helpers/e2e_prefix";

/** Syntactically plausible but non-functional — no org is contacted. */
const FAKE_PRIVATE_KEY = [
  "-----BEGIN RSA PRIVATE KEY-----",
  "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu",
  "KUpRKfFLfRYC9AIKjbJTWit+CqvjWYzvQwECAwEAAQ==",
  "-----END RSA PRIVATE KEY-----",
].join("\n");

test.describe("Plan editor — Salesforce Object is required", () => {
  test("refuses a blank object on save, and persists a padded one trimmed", async ({
    page,
    request,
  }, testInfo) => {
    const prefix = prefixFromTestInfo(testInfo);
    const mode = await fetchAuthMode(request);

    if (mode !== "none") {
      const credentials = defaultAdminCredentials();
      await loginViaApi(request, credentials);
      await loginViaUi(page, credentials.email, credentials.password);
    }

    const connection = await createConnection(request, {
      name: `${prefix}-conn`,
      instance_url: "https://example.my.salesforce.com",
      login_url: "https://login.salesforce.com",
      client_id: "3MVG9fake",
      private_key: FAKE_PRIVATE_KEY,
      username: `${prefix}@example.invalid`,
      is_sandbox: false,
    });

    const plan = await createPlan(request, {
      name: `${prefix}-plan`,
      connection_id: connection.id,
    });

    try {
      const step = await createStep(request, plan.id, {
        object_name: "Account",
        operation: "insert",
        csv_file_pattern: "accounts.csv",
      });

      // ── The API refuses a blank value on its own ─────────────────────────
      // Asserted before touching the UI so a passing UI test can never be the
      // only thing standing between an empty object_name and the database.
      for (const blank of ["", " ", "\t"]) {
        const rejected = await request.put(
          `/api/load-plans/${plan.id}/steps/${step.id}`,
          { data: { object_name: blank } },
        );
        expect(
          rejected.status(),
          `object_name ${JSON.stringify(blank)} must be rejected by the API`,
        ).toBe(422);
      }

      // ── The editor surfaces it inline ─────────────────────────────────────
      await page.goto(`/plans/${plan.id}`);

      await page
        .getByRole("button", { name: "Edit" })
        .last()
        .click();

      const dialog = page.getByRole("dialog");
      await expect(dialog.getByText("Edit Step")).toBeVisible();

      const objectField = dialog.getByLabel(/Salesforce Object/);
      await objectField.fill("");
      await dialog.getByRole("button", { name: "Save Changes" }).click();

      await expect(dialog.getByRole("alert")).toContainText(
        "Salesforce Object is required.",
      );

      // The modal stays open and nothing was written.
      await expect(dialog.getByText("Edit Step")).toBeVisible();
      const unchanged = await request.get(`/api/load-plans/${plan.id}`);
      const unchangedBody = (await unchanged.json()) as {
        load_steps: { id: string; object_name: string }[];
      };
      expect(
        unchangedBody.load_steps.find((s) => s.id === step.id)?.object_name,
      ).toBe("Account");

      // ── A padded value saves, trimmed ─────────────────────────────────────
      await objectField.fill("  Contact  ");
      await dialog.getByRole("button", { name: "Save Changes" }).click();
      await expect(dialog).toBeHidden();

      const after = await request.get(`/api/load-plans/${plan.id}`);
      const afterBody = (await after.json()) as {
        load_steps: { id: string; object_name: string }[];
      };
      expect(
        afterBody.load_steps.find((s) => s.id === step.id)?.object_name,
      ).toBe("Contact");
    } finally {
      await deletePlan(request, plan.id);
      await deleteConnection(request, connection.id);
    }
  });
});
