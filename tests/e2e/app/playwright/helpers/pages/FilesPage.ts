/**
 * FilesPage — page-object for /files.
 *
 * Wraps selectors and actions for the Files browse/preview page
 * (frontend/src/pages/FilesPage.tsx). Selectors prefer getByRole /
 * getByLabel / getByText over data-testid where ARIA disambiguates.
 */

import type { Locator, Page } from "@playwright/test";

export class FilesPage {
  readonly page: Page;

  /** The listbox containing file and directory entries. */
  readonly fileList: Locator;

  /** The source selector dropdown ("Source" label). */
  readonly sourceSelect: Locator;

  /** The storage-location subtitle ("Stored in …") — SFBL-296. */
  readonly storageLocation: Locator;

  /** The desktop "Configured in Storage Settings" deep link — SFBL-296. */
  readonly storageSettingsLink: Locator;

  constructor(page: Page) {
    this.page = page;
    this.fileList = page.getByRole("listbox", { name: "Files" });
    this.sourceSelect = page.getByLabel("Source");
    this.storageLocation = page.getByTestId("storage-location");
    this.storageSettingsLink = page.getByRole("link", {
      name: "Configured in Storage Settings",
    });
  }

  /** Visible text of every option in the source selector. */
  async sourceOptionLabels(): Promise<string[]> {
    return this.sourceSelect.locator("option").allTextContents();
  }

  /** Navigate to the /files route. */
  async goto(): Promise<void> {
    await this.page.goto("/files");
  }

  /**
   * Return a locator for a specific file entry by name.
   * Matches on the visible text of the file name cell within the listbox.
   */
  fileEntry(name: string): Locator {
    return this.fileList.getByRole("option").filter({ hasText: name });
  }

  /**
   * Click on a file entry to open its preview.
   * The entry must be a file (not a directory) for the preview panel to appear.
   */
  async selectFile(name: string): Promise<void> {
    await this.fileEntry(name).click();
  }

  /**
   * Click on a directory entry to navigate into it.
   */
  async navigateInto(name: string): Promise<void> {
    await this.fileList
      .getByRole("option")
      .filter({ hasText: name })
      .click();
  }

  /**
   * Return a locator for the CSV preview panel heading row.
   * The CsvPreviewPanel renders a <table> whose first row is the header.
   */
  get previewTable(): Locator {
    return this.page.getByRole("table");
  }

  /**
   * Return a locator for the column header cell matching `name` in the preview
   * table. Uses getByRole so it works regardless of th vs. td implementation.
   */
  previewColumnHeader(name: string): Locator {
    return this.previewTable.getByRole("columnheader", { name });
  }
}
