/**
 * setup_connection.ts — Tier 2 helper for creating a bulk-loader Connection
 * row that points at the current scratch org.
 *
 * WHY this file is under app/ (not sf/):
 *   It calls the bulk loader's own API (/api/connections), which is an
 *   app-specific concern.  Salesforce-shaped scripts (jwt_smoke_test,
 *   discover_eca_consumer_key) live under sf/scripts/.
 *
 * Usage in a Tier 2 Playwright spec:
 *
 *   import { createScratchOrgConnection, deleteScratchOrgConnection } from "./setup_connection";
 *   import type { ConnectionRecord } from "./api";
 *
 *   let connection: ConnectionRecord;
 *
 *   test.beforeAll(async ({ request }) => {
 *     connection = await createScratchOrgConnection(request);
 *   });
 *
 *   test.afterAll(async ({ request }) => {
 *     if (connection) await deleteScratchOrgConnection(request, connection.id);
 *   });
 *
 * Environment variables consumed (all set by the Tier 2 workflow steps):
 *   E2E_SCRATCH_ORG_INSTANCE_URL   — scratch org instanceUrl from `sf org display`
 *   E2E_SCRATCH_ORG_LOGIN_URL      — same as instance URL for scratch orgs
 *   E2E_SCRATCH_ORG_USERNAME       — scratch org admin username
 *   E2E_BULK_LOADER_CONSUMER_KEY   — ECA consumer key from discover_eca_consumer_key.sh
 *   SFBL_E2E_BULK_LOADER_JWT_KEY   — PEM private key contents (GH secret)
 *
 * The PEM string is read directly from the env var — NOT from a temp file.
 * The /api/connections endpoint stores PEM contents (not a path).
 */

import type { APIRequestContext } from "@playwright/test";
import {
  createConnection,
  deleteConnection,
  type ConnectionRecord,
} from "./api";

// ── Env-var reader with clear error messages ──────────────────────────────────

/**
 * Read a required environment variable.
 * Throws a descriptive error if the variable is missing or empty so callers
 * get a clear message rather than a silent empty-string failure.
 */
function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `[setup_connection] required env var '${name}' is not set.\n` +
        `  In CI this is populated by the Tier 2 workflow steps.\n` +
        `  In local dev set it in your shell before running Playwright.`,
    );
  }
  return value;
}

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Create a bulk-loader Connection row for the current scratch org.
 *
 * Returns the ConnectionRecord (id, name, instance_url) so the caller can
 * store the id for teardown.
 *
 * The connection name includes a timestamp so multiple concurrent test runs
 * don't collide on the connection name field (which must be unique per the
 * API validation).
 */
export async function createScratchOrgConnection(
  request: APIRequestContext,
  nameOverride?: string,
): Promise<ConnectionRecord> {
  const instanceUrl = requireEnv("E2E_SCRATCH_ORG_INSTANCE_URL");
  const loginUrl = requireEnv("E2E_SCRATCH_ORG_LOGIN_URL");
  const username = requireEnv("E2E_SCRATCH_ORG_USERNAME");
  const clientId = requireEnv("E2E_BULK_LOADER_CONSUMER_KEY");
  // PEM string — read from memory, never written to disk by this helper.
  // The API stores PEM contents (ConnectionCreate.private_key is a plain PEM string).
  const privateKey = requireEnv("SFBL_E2E_BULK_LOADER_JWT_KEY");

  const name =
    nameOverride ?? `E2E scratch-org ${new Date().toISOString()}`;

  return createConnection(request, {
    name,
    instance_url: instanceUrl,
    login_url: loginUrl,
    client_id: clientId,
    username,
    private_key: privateKey,
    // Scratch orgs are not sandboxes.  is_sandbox controls the login URL
    // prefix logic in salesforce_auth.py; scratch orgs use the instance URL
    // directly as the token endpoint, consistent with is_sandbox=false.
    is_sandbox: false,
  });
}

/**
 * Delete a bulk-loader Connection row by ID.
 * Wraps deleteConnection from api.ts with a descriptive log on failure.
 * Ignores 404 (already deleted) — safe to call in teardown.
 */
export async function deleteScratchOrgConnection(
  request: APIRequestContext,
  id: string,
): Promise<void> {
  await deleteConnection(request, id);
}
