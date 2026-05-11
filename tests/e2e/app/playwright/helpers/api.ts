/**
 * Minimal API helper for E2E test setup (D10: app-state via API POSTs).
 *
 * Subsequent waves will expand this with createConnection(), createPlan(),
 * createStep() etc. For now it exposes only what the Tier 1a file-pane
 * canary needs: login.
 *
 * The helper uses raw `fetch` rather than the frontend's apiFetch, because
 * specs run in Node (Playwright's worker context) not a browser.
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
