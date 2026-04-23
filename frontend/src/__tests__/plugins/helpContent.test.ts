/**
 * Unit tests for the rewriteInternalLinks function.
 *
 * The function is a pure string transformer in src/utils/rewriteHelpLinks.ts.
 * The Vite plugin re-exports it; this test targets the utility directly.
 */

import { describe, it, expect } from 'vitest'
import { rewriteInternalLinks } from '../../utils/rewriteHelpLinks'

// Build a slug map for tests
function makeSlugMap(entries: Record<string, string>): Map<string, string> {
  return new Map(Object.entries(entries))
}

describe('rewriteInternalLinks', () => {
  const slugMap = makeSlugMap({
    'running-loads.md': 'running-loads',
    'connections.md': 'connections',
    'user-management.md': 'user-management',
  })

  it('rewrites a bare filename link to /help#slug', () => {
    const html = '<p>See <a href="running-loads.md">Running loads</a>.</p>'
    const result = rewriteInternalLinks(html, slugMap)
    expect(result).toContain('href="/help#running-loads"')
  })

  it('rewrites a ./ prefixed link to /help#slug', () => {
    const html = '<a href="./connections.md">Connections</a>'
    const result = rewriteInternalLinks(html, slugMap)
    expect(result).toContain('href="/help#connections"')
  })

  it('rewrites a link with an anchor to /help#slug:heading', () => {
    const html = '<a href="./running-loads.md#start-a-run">Start a run</a>'
    const result = rewriteInternalLinks(html, slugMap)
    expect(result).toContain('href="/help#running-loads:start-a-run"')
  })

  it('rewrites a bare filename link with anchor', () => {
    const html = '<a href="user-management.md#invite-a-user">Invite</a>'
    const result = rewriteInternalLinks(html, slugMap)
    expect(result).toContain('href="/help#user-management:invite-a-user"')
  })

  it('leaves anchor-only links unchanged', () => {
    const html = '<a href="#same-page-section">Section</a>'
    const result = rewriteInternalLinks(html, slugMap)
    expect(result).toContain('href="#same-page-section"')
  })

  it('leaves external http links unchanged', () => {
    const html = '<a href="https://example.com">External</a>'
    const result = rewriteInternalLinks(html, slugMap)
    expect(result).toContain('href="https://example.com"')
  })

  it('leaves external https links unchanged', () => {
    const html = '<a href="http://docs.salesforce.com">SF docs</a>'
    const result = rewriteInternalLinks(html, slugMap)
    expect(result).toContain('href="http://docs.salesforce.com"')
  })

  it('leaves links to files outside docs/usage/ (with ../) unchanged', () => {
    const html = '<a href="../deployment/docker.md">Docker guide</a>'
    const result = rewriteInternalLinks(html, slugMap)
    expect(result).toContain('href="../deployment/docker.md"')
  })

  it('leaves links to unknown .md files unchanged', () => {
    const html = '<a href="unknown-topic.md">Unknown</a>'
    const result = rewriteInternalLinks(html, slugMap)
    expect(result).toContain('href="unknown-topic.md"')
  })

  it('handles multiple links in the same HTML string', () => {
    const html =
      '<p><a href="./connections.md">Connections</a> and <a href="running-loads.md#step-1">Step 1</a></p>'
    const result = rewriteInternalLinks(html, slugMap)
    expect(result).toContain('href="/help#connections"')
    expect(result).toContain('href="/help#running-loads:step-1"')
  })

  it('preserves other attributes on anchor tags', () => {
    const html = '<a class="cross-link" href="connections.md">Connections</a>'
    const result = rewriteInternalLinks(html, slugMap)
    expect(result).toContain('href="/help#connections"')
    expect(result).toContain('class="cross-link"')
  })
})
