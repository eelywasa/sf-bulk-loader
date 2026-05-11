/**
 * API helpers for E2E test setup (D10: app-state via API POSTs).
 *
 * All helpers use Playwright's APIRequestContext so cookies / session state is
 * shared with the browser page when the same `request` fixture is reused.
 *
 * Shaped after the frontend's types (frontend/src/api/types.ts) and endpoints
 * (frontend/src/api/endpoints.ts) so additions here mirror changes there.
 */

import type { APIRequestContext } from "@playwright/test";

export interface LoginCredentials {
  email: string;
  password: string;
}

/**
 * Log in via the /api/auth/login endpoint and return the JWT access token.
 * Uses the Playwright APIRequestContext so cookies/session state is shared
 * with the browser page if needed in future.
 */
export async function loginViaApi(
  request: APIRequestContext,
  credentials: LoginCredentials,
): Promise<string> {
  const response = await request.post("/api/auth/login", {
    data: credentials,
  });
  if (!response.ok()) {
    throw new Error(
      `Login failed: ${response.status()} ${await response.text()}`,
    );
  }
  const body = (await response.json()) as { access_token?: string };
  if (!body.access_token) {
    throw new Error("Login response did not contain access_token");
  }
  return body.access_token;
}

/**
 * Default fixture credentials read from env vars with safe fallbacks for
 * local dev stacks seeded by docker-compose / the dev server's ADMIN_EMAIL /
 * ADMIN_PASSWORD vars.
 */
export function defaultAdminCredentials(): LoginCredentials {
  return {
    email: process.env.E2E_ADMIN_EMAIL ?? "admin@example.com",
    password: process.env.E2E_ADMIN_PASSWORD ?? "password",
  };
}

// ── Connections ────────────────────────────────────────────────────────────────

export interface ConnectionPayload {
  name: string;
  instance_url: string;
  login_url: string;
  client_id: string;
  private_key: string;
  username: string;
  is_sandbox?: boolean;
}

export interface ConnectionRecord {
  id: string;
  name: string;
  instance_url: string;
}

/**
 * Create a Salesforce connection record via the API and return its ID.
 *
 * In fixture mode (SF_DESCRIBE_FIXTURES_DIR set) the backend never attempts
 * to use the credentials — the connection record is only needed so the plan
 * editor can associate a connection_id and trigger the fixture-backed
 * /objects endpoint.  A stub PEM is therefore acceptable here.
 */
export async function createConnection(
  request: APIRequestContext,
  payload: ConnectionPayload,
): Promise<ConnectionRecord> {
  const response = await request.post("/api/connections/", { data: payload });
  if (!response.ok()) {
    throw new Error(
      `createConnection failed: ${response.status()} ${await response.text()}`,
    );
  }
  return (await response.json()) as ConnectionRecord;
}

/**
 * Delete a connection by ID.  Ignores 404 (already deleted).
 */
export async function deleteConnection(
  request: APIRequestContext,
  id: string,
): Promise<void> {
  const response = await request.delete(`/api/connections/${id}`);
  if (!response.ok() && response.status() !== 404) {
    throw new Error(
      `deleteConnection(${id}) failed: ${response.status()} ${await response.text()}`,
    );
  }
}

// ── Load Plans ─────────────────────────────────────────────────────────────────

export interface LoadPlanPayload {
  name: string;
  connection_id: string;
  description?: string;
  abort_on_step_failure?: boolean;
  error_threshold_pct?: number;
  max_parallel_jobs?: number;
}

export interface LoadPlanRecord {
  id: string;
  name: string;
  connection_id: string;
}

/**
 * Create a load plan record via the API and return its ID.
 */
export async function createPlan(
  request: APIRequestContext,
  payload: LoadPlanPayload,
): Promise<LoadPlanRecord> {
  const response = await request.post("/api/load-plans/", { data: payload });
  if (!response.ok()) {
    throw new Error(
      `createPlan failed: ${response.status()} ${await response.text()}`,
    );
  }
  return (await response.json()) as LoadPlanRecord;
}

/**
 * Delete a load plan by ID.  Ignores 404 (already deleted).
 */
export async function deletePlan(
  request: APIRequestContext,
  id: string,
): Promise<void> {
  const response = await request.delete(`/api/load-plans/${id}`);
  if (!response.ok() && response.status() !== 404) {
    throw new Error(
      `deletePlan(${id}) failed: ${response.status()} ${await response.text()}`,
    );
  }
}
