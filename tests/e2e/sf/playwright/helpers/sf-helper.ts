/**
 * sf-helper.ts — Salesforce-generic Playwright helpers (D12, D13).
 *
 * These helpers are app-blind: they know about Salesforce (the `sf` CLI,
 * scratch-org conventions, SOQL) but NOT about the bulk loader's own API.
 * App-specific setup lives under tests/e2e/app/playwright/helpers/.
 *
 * D12 — post-load assertion contract (locked):
 *   sf data query --target-org "$E2E_SCRATCH_ORG" --json --query "<SOQL>"
 *
 * `--target-org` is MANDATORY — never inferred from `sf` default (which points
 * at the Dev Hub, not the scratch org).  `--json` is MANDATORY for structured
 * output parsing.
 */

import { execSync } from "child_process";

// ── Types ──────────────────────────────────────────────────────────────────────

/** Raw `sf data query --json` top-level envelope. */
interface SfQueryResult {
  result: {
    records: Record<string, unknown>[];
    totalSize: number;
    done: boolean;
  };
}

// ── sfQuery ────────────────────────────────────────────────────────────────────

/**
 * Run a SOQL query against a scratch org via `sf data query` and return the
 * record array.
 *
 * Spec ref: D12 (locked).  Flag set:
 *   sf data query --target-org <target> --json --query "<soql>"
 *
 * @param soql     SOQL string (no shell quoting needed — passed via execSync
 *                 with the args array form, so it is never interpreted by a
 *                 shell).
 * @param opts     Options object.
 * @param opts.targetOrg  Override the scratch-org alias.  Defaults to
 *                        `process.env.E2E_SCRATCH_ORG`.  An error is thrown
 *                        if neither is set.
 * @returns        Array of record objects from `result.records`.
 */
export function sfQuery(
  soql: string,
  opts: { targetOrg?: string } = {},
): Record<string, unknown>[] {
  const target = opts.targetOrg ?? process.env["E2E_SCRATCH_ORG"];
  if (!target) {
    throw new Error(
      "[sfQuery] E2E_SCRATCH_ORG is not set and no targetOrg was provided.\n" +
        "  In CI this is populated by the Tier 2 workflow steps.\n" +
        "  In local dev: export E2E_SCRATCH_ORG=<scratch-org-alias>",
    );
  }

  // Pass args directly to avoid any shell-interpretation of the SOQL string.
  // execSync with a string is fine here because we build it as a single shell
  // call — but we need to quote the SOQL carefully.  Using the raw command
  // string with stdio:pipe so we get stdout as a Buffer.
  const cmd = `sf data query --target-org ${shellQuote(target)} --json --query ${shellQuote(soql)}`;

  let stdout: string;
  try {
    stdout = execSync(cmd, { encoding: "utf8", stdio: ["pipe", "pipe", "pipe"] });
  } catch (err: unknown) {
    // `sf` exits non-zero on query errors.  The error object has a `stdout`
    // property with the JSON payload (including the error message) when --json
    // is used.  Extract it so callers see the Salesforce error, not a Node one.
    const execErr = err as { stdout?: string; stderr?: string; message?: string };
    const body = execErr.stdout ?? execErr.stderr ?? execErr.message ?? String(err);
    throw new Error(`[sfQuery] sf data query failed:\n${body}`);
  }

  let parsed: SfQueryResult;
  try {
    parsed = JSON.parse(stdout) as SfQueryResult;
  } catch {
    throw new Error(
      `[sfQuery] Could not parse sf output as JSON:\n${stdout.slice(0, 500)}`,
    );
  }

  return parsed.result.records;
}

// ── shellQuote ─────────────────────────────────────────────────────────────────

/**
 * Single-quote a shell argument.  Any single-quote characters inside the string
 * are escaped with the `'\''` idiom (end-quote, literal-apostrophe, re-open-quote).
 *
 * Used to protect the SOQL string and org alias against shell interpretation
 * without bringing in a full `shell-quote` npm dependency.
 */
function shellQuote(s: string): string {
  return `'${s.replace(/'/g, "'\\''")}'`;
}

// ── Re-export legacy constant (keeps backward-compat with any code that
//    imported SF_CLI_COMMAND from here before SFBL-328 expanded the file).
export const SF_CLI_COMMAND = "sf";
