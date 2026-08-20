/**
 * Tier 1a — run detail surfaces the real failure reason (SFBL-402).
 *
 * What this proves:
 *   - A run that fails end-to-end renders its actual `error_summary` message
 *     on the run detail page, rather than the generic
 *     "Run failed. See logs for details." fallback.
 *
 * Why it matters: `RunErrorSummary` uses `extra="ignore"`, so any key written
 * by the run coordinator but not declared on the schema is persisted and then
 * silently dropped before reaching the UI. Three keys drifted that way
 * (`output_storage_error`, `unexpected_exception`, `unknown_exit`), and during
 * the incident behind SFBL-400 the operator saw only the generic fallback
 * while the real cause was invisible. This spec is the end-to-end guard on the
 * render path.
 *
 * Org-free by construction (Tier 1a): the connection carries a syntactically
 * valid but fake private key, so the JWT bearer exchange fails and the run
 * terminates with `auth_error` without ever reaching a Salesforce org.
 *
 * COVERAGE LIMIT — read before extending. This exercises the render path via
 * `auth_error`, which is reachable without an org. It does **not** reach
 * `unexpected_exception`, `output_storage_error` or `unknown_exit`, because
 * forcing those needs either a real org or fault injection the product does
 * not expose. Those three are covered at the unit and API layers:
 *   - backend/tests/test_load_runs.py  (each key survives the API)
 *   - backend/tests/test_error_summary_contract.py  (no key may drift again)
 *   - frontend RunDetail.test.tsx  (each key renders, none masks another)
 * Do not read a pass here as proof that all six keys render end-to-end.
 */

import { test, expect } from "@playwright/test";
import { fetchAuthMode, loginViaUi } from "../helpers/auth";
import {
  createConnection,
  createPlan,
  createStep,
  defaultAdminCredentials,
  loginViaApi,
} from "../helpers/api";
import { prefixFromTestInfo } from "../helpers/e2e_prefix";

/** Syntactically plausible but non-functional key — the JWT exchange must fail. */
const FAKE_PRIVATE_KEY = [
  "-----BEGIN RSA PRIVATE KEY-----",
  "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu",
  "KUpRKfFLfRYC9AIKjbJTWit+CqvjWYzvQwECAwEAAQ==",
  "-----END RSA PRIVATE KEY-----",
].join("\n");

test.describe("Run detail — error summary rendering", () => {
  test("shows the real failure reason, not the generic fallback", async ({
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
      name: `${prefix}-bad-auth`,
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
      await createStep(request, plan.id, {
        object_name: "Account",
        operation: "insert",
        csv_file_pattern: "does-not-matter.csv",
      });

      const runResponse = await request.post(`/api/load-plans/${plan.id}/run`);
      expect(runResponse.ok()).toBeTruthy();
      const run = (await runResponse.json()) as { id: string };

      // The run fails fast: the JWT exchange cannot succeed with a fake key.
      await expect
        .poll(
          async () => {
            const detail = await request.get(`/api/runs/${run.id}`);
            const body = (await detail.json()) as { status: string };
            return body.status;
          },
          { timeout: 30_000, message: "run did not reach a terminal state" },
        )
        .toBe("failed");

      const detail = await request.get(`/api/runs/${run.id}`);
      const body = (await detail.json()) as {
        error_summary: Record<string, unknown> | null;
      };

      // The API must carry a populated reason — if error_summary comes back
      // null or empty, the schema dropped it and the UI has nothing to show.
      expect(body.error_summary).not.toBeNull();
      const reasons = Object.entries(body.error_summary ?? {}).filter(
        ([key, value]) => key !== "preflight_warnings" && typeof value === "string",
      );
      expect(
        reasons.length,
        "error_summary carried no string-valued reason — the key was probably dropped by RunErrorSummary",
      ).toBeGreaterThan(0);

      await page.goto(`/runs/${run.id}`);

      // The actual message renders...
      const [, firstReason] = reasons[0] as [string, string];
      await expect(page.getByText(firstReason, { exact: false })).toBeVisible();

      // ...and the generic fallback does not. That fallback appearing here is
      // precisely the incident symptom this ticket removes.
      await expect(
        page.getByText(/Run failed\. See logs for details\./),
      ).toHaveCount(0);
    } finally {
      // Teardown note: this spec necessarily leaves its plan behind. Executing
      // the plan creates a LoadRun, and `DELETE /api/load-plans/{id}` refuses
      // with 409 while any run references it — there is no run-delete endpoint
      // to clear them first. That is deliberate product behaviour (run history
      // is an audit trail), so the teardown tolerates the 409 rather than
      // failing an otherwise-passing test. The plan carries the test prefix, so
      // it stays attributable. Any *other* status is still a real failure.
      const planDelete = await request.delete(`/api/load-plans/${plan.id}`);
      expect(
        planDelete.ok() || [404, 409].includes(planDelete.status()),
        `unexpected status deleting plan: ${planDelete.status()}`,
      ).toBeTruthy();

      // The connection is likewise still referenced by that surviving plan.
      // Deleting it currently returns 500, not a clean 409: LoadPlan.connection_id
      // is ondelete="RESTRICT" AND nullable=False, so SQLAlchemy's ORM cascade
      // tries to NULL the child FK before the DB-level RESTRICT can fire, and the
      // NOT NULL constraint raises IntegrityError. That is a real backend defect
      // (SFBL-406) but it is not what this spec is about, so teardown
      // records it without failing an otherwise-passing test. Tighten this to
      // reject 500 once the guard lands — delete_step already has the pattern.
      const connDelete = await request.delete(
        `/api/connections/${connection.id}`,
      );
      if (!connDelete.ok() && connDelete.status() !== 404) {
        // eslint-disable-next-line no-console
        console.warn(
          `teardown: connection ${connection.id} not deleted (HTTP ${connDelete.status()}) — expected while SFBL-406 stands`,
        );
      }
    }
  });
});
