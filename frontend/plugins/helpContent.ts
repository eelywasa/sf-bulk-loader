import type { Plugin } from 'vite'
import { readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import matter from 'gray-matter'
import { unified } from 'unified'
import remarkParse from 'remark-parse'
import remarkGfm from 'remark-gfm'
import remarkRehype from 'remark-rehype'
import rehypeSlug from 'rehype-slug'
import rehypeStringify from 'rehype-stringify'
import type { HelpTopic, HelpHeading, HelpContentIndex } from '../src/types/help'
import { rewriteInternalLinks } from '../src/utils/rewriteHelpLinks'

export type { HelpTopic, HelpHeading, HelpContentIndex }
export { rewriteInternalLinks }

const VIRTUAL_ID = 'virtual:help-content'
const RESOLVED_ID = '\0virtual:help-content'

function extractHeadings(html: string): HelpHeading[] {
  const headings: HelpHeading[] = []
  const re = /<h([1-6])[^>]*\bid="([^"]*)"[^>]*>([\s\S]*?)<\/h\1>/g
  let m: RegExpExecArray | null
  while ((m = re.exec(html)) !== null) {
    headings.push({
      level: parseInt(m[1], 10),
      id: m[2],
      text: m[3].replace(/<[^>]+>/g, '').trim(),
    })
  }
  return headings
}

function stripHtml(html: string): string {
  return html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
}

async function buildContentIndex(docsDir: string): Promise<HelpContentIndex> {
  const processor = unified()
    .use(remarkParse)
    .use(remarkGfm)
    .use(remarkRehype, { allowDangerousHtml: true })
    .use(rehypeSlug)
    .use(rehypeStringify, { allowDangerousHtml: true })

  const files = readdirSync(docsDir)
    .filter((f) => f.endsWith('.md'))
    .map((f) => resolve(docsDir, f))

  // First pass: build basename → slug map
  const slugMap = new Map<string, string>()
  for (const filePath of files) {
    const raw = readFileSync(filePath, 'utf-8')
    const { data } = matter(raw)
    if (data.slug) {
      const basename = filePath.split('/').pop()!
      slugMap.set(basename, String(data.slug))
    }
  }

  const topics: HelpTopic[] = []

  // Second pass: render markdown → HTML and rewrite internal links
  for (const filePath of files) {
    const raw = readFileSync(filePath, 'utf-8')
    const { data, content } = matter(raw)

    if (!data.slug || !data.title) continue

    const vfile = await processor.process(content)
    const rawHtml = String(vfile)
    const html = rewriteInternalLinks(rawHtml, slugMap)

    topics.push({
      slug: String(data.slug),
      title: String(data.title),
      nav_order: Number(data.nav_order ?? 999),
      tags: Array.isArray(data.tags) ? data.tags.map(String) : [],
      summary: String(data.summary ?? ''),
      required_permission: data.required_permission ? String(data.required_permission) : undefined,
      html,
      headings: extractHeadings(html),
      bodyText: stripHtml(html),
    })
  }

  topics.sort((a, b) => a.nav_order - b.nav_order)
  return { topics }
}

export function helpContentPlugin(): Plugin {
  let docsDir: string

  return {
    name: 'help-content',
    configResolved(config) {
      docsDir = resolve(config.root, '../docs/usage')
    },
    resolveId(id) {
      if (id === VIRTUAL_ID) return RESOLVED_ID
    },
    async load(id) {
      if (id !== RESOLVED_ID) return
      const index = await buildContentIndex(docsDir)
      return `export default ${JSON.stringify(index)}`
    },
  }
}
