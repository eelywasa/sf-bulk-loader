/**
 * setup-connection-contract.spec.ts — SFBL-327
 *
 * Contract test for the Tier 2 Connection helper (setup_connection.ts).
 *
 * What this proves WITHOUT needing a live scratch org:
 *   1. A deliberately-broken payload (missing required field) sent to
 *      POST /api/connections returns HTTP 422 — confirming the TypeScript
 *      payload shape is in sync with backend/app/schemas/connection.py.
 *   2. The `createScratchOrgConnection` function throws a clear error when a
 *      required env var is absent — confirming the fail-fast env-var guard
 *      works before any network call is made.
 *
 * Tier placement:
 *   This spec is in tier-2/ because it exercises the setup_connection helper
 *   that is used exclusively by Tier 2 specs.  It does NOT require a scratch
 *   org — it runs against the standard app stack (same as tier-1a/1b).
 *   All network calls use a test-local APIRequestContext.
 *
 * Auth:
 *   The spec uses auth_mode=none (APP_DISTRIBUTION=desktop) where available,
 *   or provides no auth header.  A 422 is returned before auth is checked
 *   since Pydantic validation runs before endpoint handler code.
 */

import { test, expect } from "@playwright/test";
import {
  labelLayer,
  labelTier,
  linkIssue,
} from "../../../sf/playwright/helpers/allure";

// SFBL-334 Allure annotations — applied to every test in this file via a
// top-level beforeEach. Taxonomy: tier=2, layer=e2e, issue=SFBL-327.
test.beforeEach(async ({}, testInfo) => {
  linkIssue(testInfo, "SFBL-327");
  labelTier(testInfo, "2");
  labelLayer(testInfo, "e2e");
});

// ---------------------------------------------------------------------------
// 1. Schema contract — missing required field → 422
// ---------------------------------------------------------------------------

test.describe("POST /api/connections schema contract", () => {
  test("missing private_key returns 422", async ({ request }) => {
    // Send a payload that omits `private_key` — required by ConnectionCreate.
    // The backend should return 422 Unprocessable Entity (Pydantic validation).
    const response = await request.post("/api/connections/", {
      data: {
        name: "contract-test-missing-key",
        instance_url: "https://test.salesforce.com",
        login_url: "https://test.salesforce.com",
        client_id: "fake-client-id",
        username: "test@example.com",
        // private_key deliberately omitted
        is_sandbox: false,
      },
    });

    // 422 means Pydantic rejected the payload — the schema contract is intact.
    // 401/403 would mean auth ran first, which is also acceptable as long as
    // the payload was at least partially parsed; but 422 is the expected response.
    const status = response.status();
    expect(
      status,
      `Expected 422 (schema validation) but got ${status}. ` +
        `If auth is enabled in this stack, ensure the test stack uses ` +
        `APP_DISTRIBUTION=desktop (auth_mode=none).`,
    ).toBe(422);
  });

  test("missing name returns 422", async ({ request }) => {
    const response = await request.post("/api/connections/", {
      data: {
        // name deliberately omitted
        instance_url: "https://test.salesforce.com",
        login_url: "https://test.salesforce.com",
        client_id: "fake-client-id",
        username: "test@example.com",
        private_key: "-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----",
        is_sandbox: false,
      },
    });
    expect(response.status()).toBe(422);
  });

  test("missing instance_url returns 422", async ({ request }) => {
    const response = await request.post("/api/connections/", {
      data: {
        name: "contract-test-no-url",
        // instance_url deliberately omitted
        login_url: "https://test.salesforce.com",
        client_id: "fake-client-id",
        username: "test@example.com",
        private_key: "-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----",
        is_sandbox: false,
      },
    });
    expect(response.status()).toBe(422);
  });

  test("completely empty payload returns 422", async ({ request }) => {
    const response = await request.post("/api/connections/", {
      data: {},
    });
    expect(response.status()).toBe(422);
  });
});

// ---------------------------------------------------------------------------
// 2. Env-var guard — createScratchOrgConnection throws on missing env var
// ---------------------------------------------------------------------------

test.describe("createScratchOrgConnection env-var guard", () => {
  test("throws a clear error when E2E_SCRATCH_ORG_INSTANCE_URL is unset", async ({
    request,
  }) => {
    // Temporarily unset the env var to simulate a misconfigured CI step.
    // We import the module inline so the deletion is effective before the call.
    const original = process.env["E2E_SCRATCH_ORG_INSTANCE_URL"];
    delete process.env["E2E_SCRATCH_ORG_INSTANCE_URL"];

    try {
      // Dynamically import so the module-level env reads happen inside this test.
      const mod = await import("../helpers/setup_connection");
      await expect(
        mod.createScratchOrgConnection(request),
      ).rejects.toThrow("E2E_SCRATCH_ORG_INSTANCE_URL");
    } finally {
      // Restore env var so subsequent tests are unaffected.
      if (original !== undefined) {
        process.env["E2E_SCRATCH_ORG_INSTANCE_URL"] = original;
      }
    }
  });

  test("throws a clear error when SFBL_E2E_BULK_LOADER_JWT_KEY is unset", async ({
    request,
  }) => {
    // Set the other required vars so only JWT_KEY is missing.
    const vars = {
      E2E_SCRATCH_ORG_INSTANCE_URL: "https://test.salesforce.com",
      E2E_SCRATCH_ORG_LOGIN_URL: "https://test.salesforce.com",
      E2E_SCRATCH_ORG_USERNAME: "test@example.com",
      E2E_BULK_LOADER_CONSUMER_KEY: "fake-consumer-key",
    };
    const originals: Record<string, string | undefined> = {};
    for (const [k, v] of Object.entries(vars)) {
      originals[k] = process.env[k];
      process.env[k] = v;
    }
    const origJwt = process.env["SFBL_E2E_BULK_LOADER_JWT_KEY"];
    delete process.env["SFBL_E2E_BULK_LOADER_JWT_KEY"];

    try {
      const mod = await import("../helpers/setup_connection");
      await expect(
        mod.createScratchOrgConnection(request),
      ).rejects.toThrow("SFBL_E2E_BULK_LOADER_JWT_KEY");
    } finally {
      for (const [k, v] of Object.entries(originals)) {
        if (v !== undefined) process.env[k] = v;
        else delete process.env[k];
      }
      if (origJwt !== undefined) process.env["SFBL_E2E_BULK_LOADER_JWT_KEY"] = origJwt;
    }
  });
});
