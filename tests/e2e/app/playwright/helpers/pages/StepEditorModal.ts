/**
 * StepEditorModal — page-object for the Add/Edit Step modal in PlanEditor.
 *
 * The modal is mounted by frontend/src/pages/PlanEditor.tsx and rendered by
 * frontend/src/components/StepEditorModal.tsx.  It wraps the selectors and
 * actions needed by Tier 1b specs so the spec files stay readable and
 * selector details are centralised here.
 *
 * Selectors follow Playwright best-practice priority:
 *   getByRole / getByLabel  >  getByText  >  getByTestId  >  CSS
 */

import type { Locator, Page } from "@playwright/test";

export class StepEditorModal {
  readonly page: Page;

  constructor(page: Page) {
    this.page = page;
  }

  // ── Opening the modal ──────────────────────────────────────────────────────

  /**
   * Click the "Add Step" button on the PlanEditor page to open the modal.
   *
   * `StepList` renders TWO "Add Step" buttons when the plan has no steps yet:
   * one in the Card's action slot (header) and a duplicate in the empty-state
   * panel inside the card body (see `frontend/src/components/StepList.tsx`).
   * Both call the same `onAddStep` callback, so either opens the same modal.
   * Use `.first()` to disambiguate Playwright's strict-mode locator without
   * binding the test to which button happens to render first — the canary
   * doesn't care which one it clicks.
   */
  async openAddStep(): Promise<void> {
    await this.page
      .getByRole("button", { name: "Add Step" })
      .first()
      .click();
    // Wait until the modal heading is visible
    await this.page
      .getByRole("heading", { name: "Add Step" })
      .waitFor({ state: "visible" });
  }

  // ── Modal presence ─────────────────────────────────────────────────────────

  /** The dialog element wrapping the modal. */
  get dialog(): Locator {
    return this.page.getByRole("dialog", { name: /Add Step|Edit Step/ });
  }

  // ── Object name combobox ───────────────────────────────────────────────────

  /**
   * The Salesforce Object combobox input.
   * ComboInput renders an <input id="step-object"> (see StepEditorModal.tsx:211).
   */
  get objectInput(): Locator {
    return this.page.getByLabel("Salesforce Object");
  }

  /**
   * Type a partial object name into the combobox to trigger filtering, then
   * pick the matching option from the dropdown suggestion list.
   *
   * @param objectName   Exact API name of the object to select (e.g. "Account")
   */
  async selectObject(objectName: string): Promise<void> {
    await this.objectInput.fill(objectName);
    // The ComboInput renders options as <li> elements in a dropdown listbox.
    // Wait for the option and click it.
    //
    // `exact: true` is critical: the captured fixture contains 38 objects
    // whose names start with "Account" (AccountBrand, AccountContactRelation,
    // AccountAccountRelationFeed, …).  Without exact matching, Playwright's
    // strict-mode locator resolution fails with a 38-element match list
    // (observed on PR #88 CI run 25672963085 / job 75364478654).
    const option = this.page.getByRole("option", { name: objectName, exact: true });
    await option.waitFor({ state: "visible", timeout: 10_000 });
    await option.click();
  }

  /**
   * Return the combobox suggestion list that appears when the user interacts
   * with the object combobox.  Used to assert option presence without clicking.
   */
  get objectDropdown(): Locator {
    return this.page.getByRole("listbox");
  }

  /**
   * Open the object combobox's options dropdown.
   *
   * The underlying `ComboInput` (see `frontend/src/components/ui/ComboInput.tsx`)
   * opens its listbox on:
   *   - a non-empty `onChange` (typing into the input),
   *   - the `ArrowDown` key while the input is focused, or
   *   - a click on the "Show options" chevron button.
   *
   * It does NOT open on `focus` alone, so a bare `objectInput.click()` won't
   * reveal the dropdown.  This helper uses the chevron button — it's the most
   * intent-clear option and doesn't rely on filtering through a search term
   * that the spec doesn't really need.
   */
  async openObjectOptions(): Promise<void> {
    await this.objectInput.click();
    await this.dialog
      .getByRole("button", { name: "Show options" })
      .first()
      .click();
    await this.objectDropdown.waitFor({ state: "visible", timeout: 15_000 });
  }

  /**
   * Focused option locator within the object dropdown suggestion list.
   * Matches option elements by text so callers can assert visibility.
   */
  objectOption(name: string): Locator {
    return this.page.getByRole("option", { name, exact: true });
  }

  // ── Operation select ───────────────────────────────────────────────────────

  /**
   * The Operation <select> element (id="step-operation").
   * Use `selectOption()` to change the operation.
   */
  get operationSelect(): Locator {
    return this.page.getByLabel("Operation");
  }

  /**
   * Select an operation value (e.g. "upsert", "insert", "update").
   */
  async selectOperation(operation: string): Promise<void> {
    await this.operationSelect.selectOption(operation);
  }

  // ── External ID combobox (upsert only) ────────────────────────────────────

  /**
   * The External ID Field combobox input (id="step-ext-id").
   * Only visible when operation === 'upsert'.
   */
  get externalIdInput(): Locator {
    return this.page.getByLabel("External ID Field");
  }

  // ── Modal dismiss ──────────────────────────────────────────────────────────

  /**
   * Close the modal without saving.
   *
   * The Cancel button lives in the modal's footer.  When operation === 'upsert'
   * the modal body is tall enough that the footer sits outside the default
   * Playwright viewport (1280×720 in CI).  Page-level scroll doesn't help —
   * the modal is fixed-position — so we explicitly scroll the dialog's own
   * scroll container before clicking.
   *
   * Observed on PR #88 CI run 25673555777 / job 75366654421: the click
   * retried 114 times over 60s with "element is outside of the viewport"
   * before the test timed out.
   *
   * Scoping the locator to `dialog` also future-proofs against any other
   * page-level "Cancel" buttons (e.g. unsaved-changes confirmation banners).
   */
  async cancel(): Promise<void> {
    const cancel = this.dialog.getByRole("button", { name: "Cancel" });
    await cancel.scrollIntoViewIfNeeded();
    await cancel.click();
  }
}
