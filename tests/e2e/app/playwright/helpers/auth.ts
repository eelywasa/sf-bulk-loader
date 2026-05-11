/**
 * Shared auth helpers for E2E specs.
 *
 * Extracted from tier-1a/files-pane.spec.ts (SFBL-318) so that Tier 1b specs
 * can reuse the same mode-aware login pattern without duplicating it.
 *
 * Both helpers are designed for use in any Playwright spec project —
 * they rely only on standard Playwright fixtures (page / request).
 */

import type {
  APIRequestContext,
  Page,
} from "@playwright/test";

/**
 * Fetch the backend's runtime config (`/api/runtime`) to discover the active
 * auth mode.  Unauthenticated endpoint — safe to call before any login.
 *
 * Returns the `auth_mode` string ("none" | "local") or an empty string when
 * the field is absent.
 */
export async function fetchAuthMode(
  request: APIRequestContext,
): Promise<string> {
  const response = await request.get("/api/runtime");
  if (!response.ok()) {
    throw new Error(
      `GET /api/runtime returned HTTP ${response.status()}; cannot determine auth mode`,
    );
  }
  const body = (await response.json()) as { auth_mode?: string };
  return body.auth_mode ?? "";
}

/**
 * Log in via the UI login form.
 * Returns after the browser has navigated away from /login.
 *
 * @param page      Playwright page
 * @param email     User email address
 * @param password  User password
 */
export async function loginViaUi(
  page: Page,
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
