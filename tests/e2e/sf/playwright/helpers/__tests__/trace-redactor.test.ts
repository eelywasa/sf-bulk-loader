/**
 * Unit tests for trace-redactor.ts (SFBL-334 / SFBL-346).
 *
 * Each redaction rule is asserted in isolation against `redactText`. A
 * round-trip test then proves `redactTraceZip` rewrites a real on-disk
 * zip end-to-end with planted secrets in multiple entries.
 *
 * Runs under the `helpers-unit` Playwright project — no browser fixtures
 * needed, just the @playwright/test runner for test()/expect().
 */

import { test, expect } from "@playwright/test";
import AdmZip from "adm-zip";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import {
  redactText,
  redactTraceZip,
  REDACTION_PATTERNS,
} from "../trace-redactor";

// ─── redactText — per-pattern isolation ─────────────────────────────────────

test.describe("redactText", () => {
  test("scrubs RSA PEM private key block", () => {
    const input = [
      "Some log line",
      "-----BEGIN RSA PRIVATE KEY-----",
      "MIIEpAIBAAKCAQEAxxx...",
      "yyy...",
      "-----END RSA PRIVATE KEY-----",
      "trailing log",
    ].join("\n");
    const out = redactText(input);
    expect(out).not.toContain("BEGIN RSA PRIVATE KEY");
    expect(out).not.toContain("END RSA PRIVATE KEY");
    expect(out).not.toContain("MIIEpAIBAAKCAQEAxxx");
    expect(out).toContain("<REDACTED-PEM>");
    expect(out).toContain("trailing log");
  });

  test("scrubs Bearer token", () => {
    const input = "Authorization: Bearer abc123XYZ.tokendata-here";
    const out = redactText(input);
    expect(out).not.toContain("abc123XYZ.tokendata-here");
    // Either the http-auth-header rule OR the bearer-token rule fired —
    // both are acceptable; the assertion is that the token is gone.
    expect(out).toContain("<REDACTED>");
  });

  test("scrubs Cookie header (raw HTTP format)", () => {
    const input = "Cookie: sessionId=ABCdef.GHIjkl; auth=mysecret";
    const out = redactText(input);
    expect(out).not.toContain("sessionId=ABCdef.GHIjkl");
    expect(out).not.toContain("mysecret");
    expect(out).toContain("Cookie: <REDACTED>");
  });

  test("scrubs Set-Cookie header (raw HTTP format)", () => {
    const input = "Set-Cookie: sid=xyz; Path=/; HttpOnly";
    const out = redactText(input);
    expect(out).not.toContain("sid=xyz");
    expect(out).toContain("Set-Cookie: <REDACTED>");
  });

  test("scrubs JSON-encoded Authorization header pair (Playwright trace.network format)", () => {
    const input =
      '{"name":"authorization","value":"Bearer eyJ.somejwt.material"},{"name":"accept","value":"*/*"}';
    const out = redactText(input);
    expect(out).not.toContain("eyJ.somejwt.material");
    // Pair pattern fires first and rewrites the value; the `accept` header
    // is not Authorization/Cookie/Set-Cookie so it remains.
    expect(out).toContain('"value":"<REDACTED>"');
    expect(out).toContain('"name":"accept"');
  });

  test("scrubs JSON-encoded Cookie / Set-Cookie pairs (case-insensitive)", () => {
    const input =
      '{"name":"Cookie","value":"sid=abc"},{"name":"Set-Cookie","value":"x=y"}';
    const out = redactText(input);
    expect(out).not.toContain("sid=abc");
    expect(out).not.toContain("x=y");
    expect(out.match(/<REDACTED>/g)?.length).toBe(2);
  });

  test("scrubs long base64-shaped run (>200 chars)", () => {
    const longBase64 = "A".repeat(220) + "BcD9+/=";
    const input = `before ${longBase64} after`;
    const out = redactText(input);
    expect(out).not.toContain(longBase64);
    expect(out).toContain("<REDACTED-B64>");
    expect(out).toContain("before ");
    expect(out).toContain(" after");
  });

  test("does NOT scrub short base64-shaped run (≤200 chars)", () => {
    const short = "A".repeat(180);
    const out = redactText(short);
    expect(out).toBe(short);
  });

  test("does NOT scrub innocuous log content", () => {
    const input = "GET /api/health 200 OK in 4ms";
    expect(redactText(input)).toBe(input);
  });

  test("is idempotent — running twice yields the same output", () => {
    const input = [
      "-----BEGIN CERTIFICATE-----",
      "x".repeat(80),
      "-----END CERTIFICATE-----",
      "Bearer abcdef123456789",
      'Cookie: sid=zzz',
    ].join("\n");
    const once = redactText(input);
    const twice = redactText(once);
    expect(twice).toBe(once);
  });

  test("REDACTION_PATTERNS contains every expected rule by name", () => {
    const names = REDACTION_PATTERNS.map((r) => r.name);
    expect(names).toEqual([
      "pem-block",
      "bearer-token",
      "http-auth-header",
      "http-cookie-header",
      "http-set-cookie-header",
      "json-header-pair",
      "long-base64",
    ]);
  });
});

// ─── redactTraceZip — zip round-trip ────────────────────────────────────────

test.describe("redactTraceZip", () => {
  let tmpDir: string;

  test.beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "sfbl-redactor-test-"));
  });

  test.afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  test("rewrites text entries and leaves binary entries untouched", () => {
    const zipPath = path.join(tmpDir, "trace.zip");
    const zip = new AdmZip();

    // Three planted text entries, each with a different secret pattern.
    zip.addFile(
      "trace.network",
      Buffer.from(
        '[{"name":"authorization","value":"Bearer s3cret-tok"}]\n',
        "utf8",
      ),
    );
    zip.addFile(
      "resources/abc123.json",
      Buffer.from(
        "-----BEGIN PRIVATE KEY-----\npayload\n-----END PRIVATE KEY-----\n",
        "utf8",
      ),
    );
    zip.addFile(
      "trace.trace",
      Buffer.from(
        `{"console":"log","text":"Cookie: sid=zzz"}\n`,
        "utf8",
      ),
    );

    // One binary entry — a small PNG-ish payload with NUL bytes. Must survive
    // the redactor untouched.
    const binary = Buffer.from([
      0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0xff, 0xab,
    ]);
    zip.addFile("resources/screenshot.png", binary);

    zip.writeZip(zipPath);

    const stats = redactTraceZip(zipPath);

    expect(stats.textEntriesScrubbed).toBe(3);
    expect(stats.binaryEntriesSkipped).toBe(1);

    // Re-read the zip and verify each entry's post-state.
    const after = new AdmZip(zipPath);
    const network = after.getEntry("trace.network")!.getData().toString("utf8");
    const resource = after
      .getEntry("resources/abc123.json")!
      .getData()
      .toString("utf8");
    const trace = after.getEntry("trace.trace")!.getData().toString("utf8");
    const png = after.getEntry("resources/screenshot.png")!.getData();

    expect(network).not.toContain("s3cret-tok");
    expect(network).toContain("<REDACTED>");
    expect(resource).not.toContain("BEGIN PRIVATE KEY");
    expect(resource).toContain("<REDACTED-PEM>");
    expect(trace).not.toContain("sid=zzz");
    expect(png).toEqual(binary);
  });

  test("idempotent — second pass yields zero changes", () => {
    const zipPath = path.join(tmpDir, "trace.zip");
    const zip = new AdmZip();
    zip.addFile(
      "trace.network",
      Buffer.from('Cookie: sid=xyz\nAuthorization: Bearer abc.def.ghi', "utf8"),
    );
    zip.writeZip(zipPath);

    const first = redactTraceZip(zipPath);
    expect(first.textEntriesScrubbed).toBe(1);

    const second = redactTraceZip(zipPath);
    expect(second.textEntriesScrubbed).toBe(0);
  });

  test("no-op on a zip with no secret material", () => {
    const zipPath = path.join(tmpDir, "trace.zip");
    const zip = new AdmZip();
    zip.addFile(
      "trace.trace",
      Buffer.from("GET /api/health 200 OK\n", "utf8"),
    );
    zip.writeZip(zipPath);

    const stats = redactTraceZip(zipPath);
    expect(stats.textEntriesScrubbed).toBe(0);
  });
});
