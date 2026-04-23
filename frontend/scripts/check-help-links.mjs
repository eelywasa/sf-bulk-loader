#!/usr/bin/env node
/**
 * SFBL-218: CI drift check for in-app help content.
 *
 * Checks all docs/usage/*.md files for:
 *   A) Valid required_permission values (must match ALL_PERMISSION_KEYS in backend)
 *   B) Internal markdown links resolve to existing files
 *   C) Heading anchor references resolve in target files
 *   D) Required frontmatter fields (slug, title) are present
 *
 * Exit code 0 = all checks pass
 * Exit code 1 = one or more failures
 *
 * Run from repo root: node frontend/scripts/check-help-links.mjs
 */

import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { resolve, dirname, join, extname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const USAGE_DIR = join(REPO_ROOT, 'docs', 'usage');
const PERMISSIONS_FILE = join(REPO_ROOT, 'backend', 'app', 'auth', 'permissions.py');

// ─── Helpers ────────────────────────────────────────────────────────────────

/**
 * Parse YAML frontmatter from a markdown file.
 * Supports the simple key: value syntax used in docs/usage/*.md
 * Returns { data, content } where data is an object of frontmatter fields.
 */
function parseFrontmatter(fileContent) {
  const match = fileContent.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
  if (!match) {
    return { data: {}, content: fileContent };
  }
  const rawYaml = match[1];
  const content = match[2] || '';
  const data = {};

  // Parse simple key: value pairs, handling multi-line block scalars (>-)
  const lines = rawYaml.split('\n');
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    // Skip empty lines
    if (!line.trim()) { i++; continue; }

    // Key: value pair
    const kvMatch = line.match(/^(\w[\w_]*)\s*:\s*(.*)/);
    if (!kvMatch) { i++; continue; }

    const key = kvMatch[1];
    let value = kvMatch[2].trim();

    // Block scalar: >- or > (fold to single line)
    if (value === '>-' || value === '>') {
      const blockLines = [];
      i++;
      while (i < lines.length && (lines[i].startsWith('  ') || lines[i].trim() === '')) {
        blockLines.push(lines[i].trim());
        i++;
      }
      data[key] = blockLines.join(' ').trim();
      continue;
    }

    // Inline list: [a, b, c]
    if (value.startsWith('[') && value.endsWith(']')) {
      const inner = value.slice(1, -1);
      data[key] = inner.split(',').map(s => s.trim()).filter(Boolean);
      i++;
      continue;
    }

    // Plain value (strip optional quotes)
    data[key] = value.replace(/^['"]|['"]$/g, '');
    i++;
  }

  return { data, content };
}

/**
 * Extract all permission keys from ALL_PERMISSION_KEYS frozenset block in permissions.py.
 *
 * The file uses module-level constants (e.g. CONNECTIONS_VIEW = "connections.view")
 * that are then referenced inside ALL_PERMISSION_KEYS = frozenset({CONNECTIONS_VIEW, ...}).
 * We first build a map of constant names → string values, then resolve the frozenset members.
 * Falls back to treating quoted strings inside the block directly if any are found.
 */
function extractPermissionKeys(pySource) {
  // Step 1: build map of CONSTANT_NAME -> "string.value" from module-level assignments
  const constMap = new Map();
  const constRe = /^([A-Z_]+)\s*=\s*['"]([a-z_.]+)['"]/gm;
  let cm;
  while ((cm = constRe.exec(pySource)) !== null) {
    constMap.set(cm[1], cm[2]);
  }

  // Step 2: find the ALL_PERMISSION_KEYS = frozenset({ ... }) block
  const blockMatch = pySource.match(/ALL_PERMISSION_KEYS\s*(?::\s*frozenset\[str\])?\s*=\s*frozenset\s*\(\s*\{([\s\S]*?)\}\s*\)/);
  if (!blockMatch) {
    throw new Error('Could not find ALL_PERMISSION_KEYS in permissions.py');
  }
  const block = blockMatch[1];

  const keys = new Set();

  // Try string literals first (e.g. "connections.view" directly in the block)
  const strRe = /['"]([a-z_.]+)['"]/g;
  let sm;
  while ((sm = strRe.exec(block)) !== null) {
    keys.add(sm[1]);
  }

  // Also resolve variable references (e.g. CONNECTIONS_VIEW,)
  const varRe = /\b([A-Z_]+)\b/g;
  let vm;
  while ((vm = varRe.exec(block)) !== null) {
    if (constMap.has(vm[1])) {
      keys.add(constMap.get(vm[1]));
    }
  }

  return keys;
}

/**
 * Convert a heading text to a GitHub-style anchor slug.
 * - lowercase
 * - replace sequences of non-alphanumeric chars with a single hyphen
 * - strip leading/trailing hyphens
 */
function headingToSlug(text) {
  // Strip markdown formatting (bold, code, links, etc.)
  let plain = text
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')  // [text](url) -> text
    .replace(/[`*_~]/g, '')                    // inline code/bold/italic
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>');

  return plain
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

/**
 * Extract all heading anchors from markdown content.
 * Returns a Set of slug strings.
 */
function extractHeadingAnchors(content) {
  const anchors = new Set();
  const re = /^#{1,6}\s+(.+)$/gm;
  let m;
  while ((m = re.exec(content)) !== null) {
    anchors.add(headingToSlug(m[1]));
  }
  return anchors;
}

/**
 * Extract all markdown links from content.
 * Returns array of { text, href, raw } objects.
 * Skips image links (![...]).
 */
function extractLinks(content) {
  const links = [];
  // Match [text](href) but not ![text](href) (images)
  const re = /(?<!\!)\[([^\]]*)\]\(([^)]+)\)/g;
  let m;
  while ((m = re.exec(content)) !== null) {
    const text = m[1];
    const full = m[2].trim();
    // Split href from optional title: href "title" or href 'title'
    const hrefMatch = full.match(/^(\S+)(?:\s+["'][^"']*["'])?$/);
    const href = hrefMatch ? hrefMatch[1] : full;
    links.push({ text, href, raw: m[0] });
  }
  return links;
}

// ─── Main ────────────────────────────────────────────────────────────────────

const errors = [];
let filesChecked = 0;

// Load permissions
let validPermissionKeys;
try {
  const pySource = readFileSync(PERMISSIONS_FILE, 'utf8');
  validPermissionKeys = extractPermissionKeys(pySource);
} catch (err) {
  console.error(`Failed to read permissions file: ${err.message}`);
  process.exit(1);
}

// Load all docs/usage/*.md files
const mdFiles = readdirSync(USAGE_DIR)
  .filter(f => f.endsWith('.md'))
  .map(f => join(USAGE_DIR, f))
  .sort();

if (mdFiles.length === 0) {
  console.error(`No markdown files found in ${USAGE_DIR}`);
  process.exit(1);
}

console.log(`Checking ${mdFiles.length} help docs...`);
filesChecked = mdFiles.length;

// Cache for file anchors (path -> Set<string>)
const anchorCache = new Map();

function getAnchors(filePath) {
  if (!anchorCache.has(filePath)) {
    if (!existsSync(filePath)) {
      anchorCache.set(filePath, null); // file doesn't exist
    } else {
      const raw = readFileSync(filePath, 'utf8');
      const { content } = parseFrontmatter(raw);
      anchorCache.set(filePath, extractHeadingAnchors(content));
    }
  }
  return anchorCache.get(filePath);
}

// ─── Check D: Required frontmatter fields ────────────────────────────────────
let frontmatterErrors = 0;
for (const filePath of mdFiles) {
  const rel = filePath.replace(REPO_ROOT + '/', '');
  const raw = readFileSync(filePath, 'utf8');
  const { data } = parseFrontmatter(raw);

  for (const field of ['slug', 'title']) {
    if (!data[field]) {
      errors.push(`${rel}: missing required frontmatter field: ${field}`);
      frontmatterErrors++;
    }
  }
}

if (frontmatterErrors === 0) {
  console.log('✓ All files have required frontmatter fields (slug, title)');
} else {
  for (const err of errors.filter(e => e.includes('missing required frontmatter'))) {
    console.log(`✗ ${err}`);
  }
}

// ─── Check A: Valid required_permission values ────────────────────────────────
let permErrors = 0;
for (const filePath of mdFiles) {
  const rel = filePath.replace(REPO_ROOT + '/', '');
  const raw = readFileSync(filePath, 'utf8');
  const { data } = parseFrontmatter(raw);

  if (data.required_permission) {
    const perm = data.required_permission;
    if (!validPermissionKeys.has(perm)) {
      const msg = `${rel}: invalid required_permission "${perm}" (valid: ${[...validPermissionKeys].sort().join(', ')})`;
      errors.push(msg);
      console.log(`✗ ${msg}`);
      permErrors++;
    }
  }
}
if (permErrors === 0) {
  console.log('✓ All required_permission values are valid');
}

// ─── Check B & C: Internal links and anchors ─────────────────────────────────
let linkErrors = 0;
for (const filePath of mdFiles) {
  const rel = filePath.replace(REPO_ROOT + '/', '');
  const raw = readFileSync(filePath, 'utf8');
  const { content } = parseFrontmatter(raw);
  const fileDir = dirname(filePath);

  const links = extractLinks(content);

  for (const { href } of links) {
    // Skip external URLs
    if (href.startsWith('http://') || href.startsWith('https://')) {
      continue;
    }

    // Split path and anchor
    const hashIdx = href.indexOf('#');
    const pathPart = hashIdx === -1 ? href : href.slice(0, hashIdx);
    const anchor = hashIdx === -1 ? null : href.slice(hashIdx + 1);

    // Anchor-only link (same file)
    if (!pathPart) {
      if (anchor) {
        const anchors = getAnchors(filePath);
        if (!anchors || !anchors.has(anchor)) {
          const msg = `${rel}: broken anchor link "#${anchor}" (heading not found in same file)`;
          errors.push(msg);
          console.log(`✗ ${msg}`);
          linkErrors++;
        }
      }
      continue;
    }

    // Resolve the target file path
    const targetPath = resolve(fileDir, pathPart);

    // Check B: target file exists
    if (!existsSync(targetPath)) {
      const msg = `${rel}: broken link to ${pathPart} (file not found: ${targetPath.replace(REPO_ROOT + '/', '')})`;
      errors.push(msg);
      console.log(`✗ ${msg}`);
      linkErrors++;
      continue;
    }

    // Check C: if there's an anchor, verify it exists in the target file
    if (anchor && extname(targetPath) === '.md') {
      const anchors = getAnchors(targetPath);
      if (!anchors || !anchors.has(anchor)) {
        const targetRel = targetPath.replace(REPO_ROOT + '/', '');
        const msg = `${rel}: broken anchor "#${anchor}" in link to ${pathPart} (heading not found in ${targetRel})`;
        errors.push(msg);
        console.log(`✗ ${msg}`);
        linkErrors++;
      }
    }
  }
}

if (linkErrors === 0) {
  console.log('✓ All internal links resolve to existing files');
  console.log('✓ All heading anchors resolve');
}

// ─── Summary ─────────────────────────────────────────────────────────────────
console.log('');
if (errors.length === 0) {
  console.log(`Checked ${filesChecked} file(s). All checks passed.`);
  process.exit(0);
} else {
  console.log(`Found ${errors.length} error(s). Fix the issues above and re-run.`);
  process.exit(1);
}
