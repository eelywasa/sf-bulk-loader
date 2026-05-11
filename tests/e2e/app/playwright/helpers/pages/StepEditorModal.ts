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
   * The Cancel button in the modal footer is unclickable in the default
   * CI viewport (1280×720) when operation === 'upsert' — the modal panel
   * extends below the viewport with no internal scroll container, so the
   * footer sits outside the visible area.  `scrollIntoViewIfNeeded()`
   * does not help (Playwright reports "done scrolling" but the element
   * remains outside the viewport on a fixed-position panel).
   *
   * The modal is built on HeadlessUI `Dialog` (frontend/src/components/ui/Modal.tsx),
   * which closes on Escape natively.  Press Escape and assert the dialog
   * disappears — robust against viewport sizing.
   *
   * Observed on PR #88 CI runs 25673555777 / 25674253813.
   */
  async cancel(): Promise<void> {
    await this.page.keyboard.press("Escape");
    await this.dialog.waitFor({ state: "hidden", timeout: 5_000 });
  }
}
