/**
 * Rewrite internal usage-doc cross-links in rendered HTML.
 *
 * Rules:
 *  - `./running-loads.md`          → `/help#running-loads`
 *  - `running-loads.md`            → `/help#running-loads`
 *  - `./running-loads.md#heading`  → `/help#running-loads:heading`
 *  - `../deployment/docker.md`     → absolute GitHub URL (cross-pillar refs
 *                                    point at non-usage docs; they open on
 *                                    GitHub from inside /help)
 *  - `#same-page-anchor`           → left as-is
 *  - `http://` / `https://`        → left as-is
 *
 * @param html      The rendered HTML string
 * @param slugMap   Map of `basename → slug` for docs/usage/*.md files
 * @param repoDocsBase  Absolute base URL for non-usage docs on GitHub
 *                     (e.g. `https://github.com/eelywasa/sf-bulk-loader/blob/main/docs`)
 */
export function rewriteInternalLinks(
  html: string,
  slugMap: Map<string, string>,
  repoDocsBase = 'https://github.com/eelywasa/sf-bulk-loader/blob/main/docs',
): string {
  const linkRe = /<a\s+([^>]*\s)?href="([^"]*)"([^>]*)>/g
  return html.replace(linkRe, (_match, before: string | undefined, href: string, after: string) => {
    const b = before ?? ''
    if (href.startsWith('http://') || href.startsWith('https://') || href.startsWith('#')) {
      return `<a ${b}href="${href}"${after}>`
    }

    const hashIdx = href.indexOf('#')
    const pathPart = hashIdx === -1 ? href : href.slice(0, hashIdx)
    const anchor = hashIdx === -1 ? '' : href.slice(hashIdx + 1)

    // Cross-pillar reference: ../<pillar>/<file>.md → absolute GitHub URL
    if (pathPart.startsWith('../') && pathPart.endsWith('.md')) {
      const relFromDocs = pathPart.replace(/^\.\.\//, '')
      const newHref = anchor ? `${repoDocsBase}/${relFromDocs}#${anchor}` : `${repoDocsBase}/${relFromDocs}`
      return `<a ${b}href="${newHref}"${after}>`
    }

    // In-pillar reference: basename-only .md → /help#slug[:anchor]
    const normalised = pathPart.replace(/^\.\//, '')
    if (!normalised.endsWith('.md') || normalised.includes('/')) {
      return `<a ${b}href="${href}"${after}>`
    }
    const slug = slugMap.get(normalised)
    if (!slug) {
      return `<a ${b}href="${href}"${after}>`
    }
    const newHref = anchor ? `/help#${slug}:${anchor}` : `/help#${slug}`
    return `<a ${b}href="${newHref}"${after}>`
  })
}
