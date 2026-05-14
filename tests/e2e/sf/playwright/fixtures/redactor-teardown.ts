/**
 * redactor-teardown.ts — SFBL-334 / SFBL-346.
 *
 * Playwright `globalTeardown` entry point. Walks the test-results tree
 * after every test has finished and redacts every `*.zip` Playwright wrote
 * (trace.zip, retry traces, attached zip archives). Runs before any
 * artefact-upload step in CI, so the trace zips that reach the publish
 * workflow (and then the S3 evidence bucket) are post-redaction.
 *
 * Single registration point in `playwright.config.ts` keeps redaction
 * coverage spec-blind — adding a new test or project automatically picks
 * up the teardown without any per-spec import. The redactor function
 * itself is in `../helpers/trace-redactor.ts`; this module is just the
 * glue between Playwright's lifecycle and that pure function.
 */

import * as fs from "fs";
import * as path from "path";
import type { FullConfig } from "@playwright/test";
import { redactTraceZip, type RedactionStats } from "../helpers/trace-redactor";

/** Walks `dir` recursively, returning absolute paths of files matching `predicate`. */
function walkFiles(
  dir: string,
  predicate: (file: string) => boolean,
  out: string[] = [],
): string[] {
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    // Missing test-results/ is the common case when no tests captured a trace.
    return out;
  }
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walkFiles(full, predicate, out);
    } else if (predicate(full)) {
      out.push(full);
    }
  }
  return out;
}

export default async function redactorTeardown(config: FullConfig): Promise<void> {
  // Playwright's default test results path is `test-results/` relative to
  // the testDir. The runner exposes the resolved value on `config`.
  const root = config.rootDir;
  const resultsDir = path.resolve(root, "test-results");

  const zips = walkFiles(resultsDir, (file) => file.endsWith(".zip"));
  if (zips.length === 0) {
    return;
  }

  let totalEntries = 0;
  let totalBytes = 0;
  let totalSkipped = 0;
  const failures: string[] = [];

  for (const zip of zips) {
    try {
      const stats: RedactionStats = redactTraceZip(zip);
      totalEntries += stats.textEntriesScrubbed;
      totalBytes += stats.changedBytes;
      totalSkipped += stats.binaryEntriesSkipped;
    } catch (err) {
      failures.push(`${zip}: ${(err as Error).message}`);
    }
  }

  // Single-line summary — Playwright global-teardown output is captured by
  // CI but rarely scrolled, so we keep this short.
  // eslint-disable-next-line no-console
  console.log(
    `[trace-redactor] processed ${zips.length} zip(s); ` +
      `${totalEntries} entries scrubbed (${totalBytes >= 0 ? "+" : ""}${totalBytes} bytes), ` +
      `${totalSkipped} binary entries skipped.`,
  );

  if (failures.length > 0) {
    // Don't throw — a failure here would mask the test results, and the
    // canary scanner in publish-evidence.sh is the structural backstop.
    // Surface loudly instead so an operator notices.
    // eslint-disable-next-line no-console
    console.error(
      `[trace-redactor] WARNING: ${failures.length} zip(s) failed redaction:\n` +
        failures.map((f) => `  - ${f}`).join("\n"),
    );
  }
}
