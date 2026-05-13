/**
 * trace-redactor.ts — SFBL-334 / SFBL-346.
 *
 * App-blind, Salesforce-shaped post-process redactor for Playwright trace
 * zips. The redactor runs from `globalTeardown` after every test has
 * finished, walks `test-results/**\/*.zip`, and scrubs:
 *
 *   - Authorization / Cookie / Set-Cookie headers (HTTP-format and JSON
 *     name/value pair encodings used by Playwright's trace.network)
 *   - `Bearer <token>` strings anywhere in the trace text
 *   - PEM blocks (-----BEGIN…-----END) → `<REDACTED-PEM>`
 *   - Base64-shaped runs of 200+ chars → `<REDACTED-B64>` (catches Salesforce
 *     consumer-key + private-key material that survives a PEM-strip)
 *
 * Phase 1 minimization (drop in-trace screenshots + sources for Tier 1a/1b,
 * drop sources for Tier 2) is configured in `playwright.config.ts`. The
 * redactor is the second layer: scrub what the minimization let through.
 *
 * Not in scope here:
 *   - OCR-redaction of PNG screenshots in Tier 2 traces. Phase 1 simply
 *     trusts that the UI under test doesn't render long-lived secret
 *     material on screen. If that ever stops being true, a future story
 *     bolts an OCR pass onto `redactTraceZip` for the *.png entries.
 */

import AdmZip from "adm-zip";

// ─── Public types ────────────────────────────────────────────────────────────

export interface RedactionStats {
  /** Number of zip entries that were rewritten (non-zero bytes changed). */
  textEntriesScrubbed: number;
  /** Cumulative byte delta across rewritten entries (post - pre). */
  changedBytes: number;
  /** Number of zip entries skipped because content looked binary. */
  binaryEntriesSkipped: number;
}

// ─── Pure text redactor ──────────────────────────────────────────────────────

/**
 * Patterns used by `redactText`. Public so unit tests can assert each rule
 * fires in isolation. Order matters: PEM blocks first (most distinctive),
 * then Bearer / header patterns, then the catch-all base64 sweep.
 */
export const REDACTION_PATTERNS = [
  // PEM blocks (private keys, certs). Multi-line; covers any PEM type.
  {
    name: "pem-block",
    pattern: /-----BEGIN [A-Z0-9 ]+-----[\s\S]*?-----END [A-Z0-9 ]+-----/g,
    replacement: "<REDACTED-PEM>",
  },

  // `Bearer <token>` — catches Authorization header values + any token that
  // leaks into a console.log / exception message. Token chars per RFC 6750
  // plus the common base64-url variations.
  {
    name: "bearer-token",
    pattern: /Bearer\s+[A-Za-z0-9._~+/=-]{8,}/g,
    replacement: "Bearer <REDACTED>",
  },

  // HTTP raw header format: `Authorization: ...` (and Cookie / Set-Cookie).
  // Case-insensitive; consumes up to end-of-line.
  {
    name: "http-auth-header",
    pattern: /(Authorization:\s*)[^\r\n]+/gi,
    replacement: "$1<REDACTED>",
  },
  {
    name: "http-cookie-header",
    pattern: /(Cookie:\s*)[^\r\n]+/gi,
    replacement: "$1<REDACTED>",
  },
  {
    name: "http-set-cookie-header",
    pattern: /(Set-Cookie:\s*)[^\r\n]+/gi,
    replacement: "$1<REDACTED>",
  },

  // Playwright trace.network encodes headers as JSON:
  //   {"name":"authorization","value":"Bearer xxx"}
  // Match the name/value pair atomically so the value is scrubbed in place.
  // Header names in Playwright traces are lowercase but we accept any case.
  {
    name: "json-header-pair",
    pattern:
      /("name"\s*:\s*"(?:authorization|cookie|set-cookie)"\s*,\s*"value"\s*:\s*")[^"]*(")/gi,
    replacement: "$1<REDACTED>$2",
  },

  // Catch-all: long base64-shaped run. 200 chars is comfortably above any
  // legitimate identifier (Salesforce sessionId is ~88; OAuth tokens ~100;
  // sha256 hex is 64) but well below a leaked PEM body (~1700+).
  // The character class is base64url-safe so it doesn't break on JWTs.
  {
    name: "long-base64",
    pattern: /[A-Za-z0-9+/=_-]{200,}/g,
    replacement: "<REDACTED-B64>",
  },
] as const;

/**
 * Apply every redaction pattern to a single string. Pure function — exposed
 * for unit tests so each rule can be asserted independently.
 */
export function redactText(input: string): string {
  let out = input;
  for (const rule of REDACTION_PATTERNS) {
    out = out.replace(rule.pattern, rule.replacement);
  }
  return out;
}

// ─── Trace zip rewriter ──────────────────────────────────────────────────────

/**
 * Heuristic: treat an entry as binary when it contains a NUL byte in the
 * first 8 KiB. Playwright stores trace metadata + DOM snapshots as text;
 * PNGs and other binary attachments contain NULs immediately.
 */
function looksBinary(buf: Buffer): boolean {
  const window = buf.subarray(0, Math.min(buf.length, 8192));
  for (let i = 0; i < window.length; i++) {
    if (window[i] === 0) {
      return true;
    }
  }
  return false;
}

/**
 * Open a Playwright trace zip, redact every text entry in place, and write
 * the zip back to the same path. Idempotent — running it twice on the same
 * zip yields the same output as running it once.
 *
 * Returns counts useful for telemetry / `globalTeardown` summary logs.
 */
export function redactTraceZip(zipPath: string): RedactionStats {
  const zip = new AdmZip(zipPath);
  const stats: RedactionStats = {
    textEntriesScrubbed: 0,
    changedBytes: 0,
    binaryEntriesSkipped: 0,
  };

  for (const entry of zip.getEntries()) {
    if (entry.isDirectory) {
      continue;
    }
    const data = entry.getData();
    if (looksBinary(data)) {
      stats.binaryEntriesSkipped++;
      continue;
    }
    const original = data.toString("utf8");
    const scrubbed = redactText(original);
    if (scrubbed === original) {
      continue;
    }
    const scrubbedBuf = Buffer.from(scrubbed, "utf8");
    zip.updateFile(entry, scrubbedBuf);
    stats.textEntriesScrubbed++;
    stats.changedBytes += scrubbedBuf.length - data.length;
  }

  if (stats.textEntriesScrubbed > 0) {
    zip.writeZip(zipPath);
  }
  return stats;
}
