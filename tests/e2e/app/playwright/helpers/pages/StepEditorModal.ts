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
   * StepList renders an "Add Step" button via onAddStep — the StepList's empty
   * state also includes one; both map to the same button text.
   */
  async openAddStep(): Promise<void> {
    await this.page.getByRole("button", { name: "Add Step" }).click();
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
    const option = this.page.getByRole("option", { name: objectName });
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

  /** Close the modal without saving. */
  async cancel(): Promise<void> {
    await this.page.getByRole("button", { name: "Cancel" }).click();
  }
}
