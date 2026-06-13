#!/usr/bin/env node
/**
 * SFBL-296: regression guard against the misleading "Local Input" / "Local
 * Output" terminology creeping back into user-visible UI copy.
 *
 * In two of three deployment profiles (aws_hosted, docker self-hosted) the
 * listed files do not live on a local disk, so the "Local" qualifier is wrong.
 * SFBL-296 renamed the copy to neutral "Input" / "Output" wording; this check
 * fails CI if the old wording reappears.
 *
 * Scope: scans frontend/src for the title-case copy strings only. Lowercase
 * occurrences inside code comments / identifiers (e.g. "local INPUT_CLASS",
 * the `local_output` sentinel, "the local output directory" in a comment) are
 * intentionally NOT matched — those have no UI impact (per the ticket, code
 * identifiers/comments are out of scope). Test files are excluded.
 *
 * Exit code 0 = clean; 1 = the banned wording was found.
 *
 * Run from repo root: node frontend/scripts/check-no-local-labels.mjs
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { resolve, dirname, join, extname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const SRC_DIR = join(REPO_ROOT, 'frontend', 'src');

// Title-case copy form only — what shows up in select options, labels, headings.
const BANNED = /Local\s+(Input|Output|input|output)/;
const SCAN_EXT = new Set(['.ts', '.tsx']);

/** Recursively collect source files, skipping test directories/files. */
function collect(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) {
      if (name === '__tests__' || name === 'node_modules') continue;
      out.push(...collect(full));
    } else if (SCAN_EXT.has(extname(name)) && !name.includes('.test.')) {
      out.push(full);
    }
  }
  return out;
}

const failures = [];
for (const file of collect(SRC_DIR)) {
  const lines = readFileSync(file, 'utf8').split('\n');
  lines.forEach((line, i) => {
    if (BANNED.test(line)) {
      failures.push(`${file.replace(REPO_ROOT + '/', '')}:${i + 1}: ${line.trim()}`);
    }
  });
}

if (failures.length > 0) {
  console.error('✖ Banned "Local Input"/"Local Output" UI copy found (SFBL-296):');
  for (const f of failures) console.error(`  ${f}`);
  console.error('\nUse neutral "Input" / "Output" wording instead.');
  process.exit(1);
}

console.log('✓ No "Local Input"/"Local Output" UI copy in frontend/src.');
