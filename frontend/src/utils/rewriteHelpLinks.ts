/**
 * Rewrite internal usage-doc cross-links in rendered HTML so they navigate
 * within the /help shell instead of pointing at raw .md files.
 *
 * Rules:
 *  - `./running-loads.md`         → `/help#running-loads`
 *  - `running-loads.md`           → `/help#running-loads`
 *  - `./running-loads.md#heading` → `/help#running-loads:heading`
 *  - `#same-page-anchor`          → left as-is (works within the rendered pane)
 *  - `http://` / `https://`       → left as-is (external; HelpPage adds target=_blank)
 *  - `../deployment/docker.md`    → left as-is (outside docs/usage/, no /help slug)
 *
 * @param html     The rendered HTML string
 * @param slugMap  Map of `basename → slug` (e.g. `"running-loads.md" → "running-loads"`)
 */
export function rewriteInternalLinks(html: string, slugMap: Map<string, string>): string {
  const linkRe = /<a\s+([^>]*\s)?href="([^"]*)"([^>]*)>/g
  return html.replace(linkRe, (_match, before: string | undefined, href: string, after: string) => {
    const b = before ?? ''
    // Leave external links and anchor-only links unchanged
    if (href.startsWith('http://') || href.startsWith('https://') || href.startsWith('#')) {
      return `<a ${b}href="${href}"${after}>`
    }

    // Only rewrite links that look like relative .md files without a parent-dir traversal
    // (i.e. basename-only or ./basename, not ../something)
    const hashIdx = href.indexOf('#')
    const pathPart = hashIdx === -1 ? href : href.slice(0, hashIdx)
    const anchor = hashIdx === -1 ? '' : href.slice(hashIdx + 1)

    // Strip leading ./
    const normalised = pathPart.replace(/^\.\//, '')

    // Must end in .md and must not contain a directory separator (no ../ traversal)
    if (!normalised.endsWith('.md') || normalised.includes('/')) {
      return `<a ${b}href="${href}"${after}>`
    }

    const slug = slugMap.get(normalised)
    if (!slug) {
      // Unknown file — leave unchanged
      return `<a ${b}href="${href}"${after}>`
    }

    const newHref = anchor ? `/help#${slug}:${anchor}` : `/help#${slug}`
    return `<a ${b}href="${newHref}"${after}>`
  })
}
