/**
 * Tier 1a — PAT management UI: create + revoke flows (SFBL-369).
 *
 * What this proves:
 *   - The /settings/tokens route renders the PAT management page.
 *   - A user with tokens.manage can create a token; the copy-once modal appears
 *     with the plaintext token and the "not shown again" warning.
 *   - Dismissing the modal removes the plaintext from the page.
 *   - The newly-created token appears in the metadata table.
 *   - The user can revoke the token; after confirming, the token is marked
 *     Revoked in the table.
 *
 * Tier 1a classification: org-free flow — no Salesforce connection required.
 * Runs on every PR as part of the tier-1a suite.
 *
 * Auth mode handling:
 *   - auth_mode=none (desktop, APP_DISTRIBUTION=desktop): navigates directly to
 *     the page — the desktop user holds all permissions.
 *   - auth_mode=local: logs in via the UI as the admin user first.
 *   Mode is detected at test time from the unauthenticated /api/runtime endpoint.
 *
 * Admin credentials for auth_mode=local:
 *   E2E_ADMIN_EMAIL    (falls back to ADMIN_EMAIL from the stack's .env)
 *   E2E_ADMIN_PASSWORD (falls back to ADMIN_PASSWORD)
 */

import { test, expect } from "@playwright/test";
import { fetchAuthMode, loginViaUi } from "../helpers/auth";
import {
  labelLayer,
  labelTier,
  linkIssue,
} from "../../../sf/playwright/helpers/allure";

test.describe("PAT management — create + revoke flow", () => {
  test.beforeEach(async ({ page, request }) => {
    labelLayer("app");
    labelTier("1a");
    linkIssue("SFBL-369");

    const authMode = await fetchAuthMode(request);

    if (authMode === "local") {
      const email =
        process.env.E2E_ADMIN_EMAIL ??
        process.env.ADMIN_EMAIL ??
        "admin@example.com";
      const password =
        process.env.E2E_ADMIN_PASSWORD ??
        process.env.ADMIN_PASSWORD ??
        "changeme";
      await loginViaUi(page, email, password);
    }

    await page.goto("/settings/tokens");
    await page.waitForURL(/\/settings\/tokens/, { timeout: 15_000 });
  });

  test("page renders Personal Access Tokens heading", async ({ page }) => {
    await expect(
      page.getByRole("heading", { name: /personal access tokens/i }),
    ).toBeVisible();
  });

  test("create token shows copy-once modal with plaintext and warning", async ({
    page,
  }) => {
    // Open create modal
    await page.getByTestId("pat-new-button").click();

    // Fill in token name
    await page.getByLabel(/token name/i).fill("E2E test token");

    // Submit
    await page.getByRole("button", { name: /generate token/i }).click();

    // Copy-once modal should appear with the plaintext
    const plaintextEl = page.getByTestId("pat-plaintext");
    await expect(plaintextEl).toBeVisible({ timeout: 10_000 });
    const tokenText = await plaintextEl.textContent();
    expect(tokenText).toBeTruthy();
    expect(tokenText!.length).toBeGreaterThan(10);

    // "Not shown again" warning must be visible
    await expect(
      page.getByText(/this token will not be shown again/i),
    ).toBeVisible();

    // Copy button is present
    await expect(page.getByTestId("pat-copy-button")).toBeVisible();

    // Dismiss the modal
    await page.getByTestId("pat-copy-once-done").click();

    // Plaintext should no longer be on the page
    await expect(page.getByTestId("pat-plaintext")).not.toBeVisible();

    // The new token should appear in the table
    await expect(page.getByText("E2E test token")).toBeVisible();
    await expect(page.getByText("Active")).toBeVisible();
  });

  test("revoke token shows confirmation and marks token Revoked", async ({
    page,
  }) => {
    // First create a token to revoke
    await page.getByTestId("pat-new-button").click();
    await page.getByLabel(/token name/i).fill("Token to revoke");
    await page.getByRole("button", { name: /generate token/i }).click();

    // Wait for copy-once modal and dismiss
    await expect(page.getByTestId("pat-plaintext")).toBeVisible({
      timeout: 10_000,
    });
    await page.getByTestId("pat-copy-once-done").click();

    // Find the revoke button for our token (it appears in the table row)
    // The row will have the token name and an active Revoke button
    const revokeBtn = page
      .getByRole("row", { name: /token to revoke/i })
      .getByRole("button", { name: /revoke/i });
    await expect(revokeBtn).toBeVisible({ timeout: 10_000 });
    await revokeBtn.click();

    // Confirmation dialog
    const confirmBtn = page.getByTestId("pat-revoke-confirm");
    await expect(confirmBtn).toBeVisible();
    await confirmBtn.click();

    // Token should now show as Revoked in the table
    await expect(
      page
        .getByRole("row", { name: /token to revoke/i })
        .getByText("Revoked"),
    ).toBeVisible({ timeout: 10_000 });

    // Revoke button should be gone for the revoked token
    await expect(revokeBtn).not.toBeVisible();
  });
});
