import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import clsx from 'clsx'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import { faBars, faXmark, faArrowLeft } from '@fortawesome/free-solid-svg-icons'
import helpContent from 'virtual:help-content'
import { usePermission, usePermissions } from '../hooks/usePermission'
import { useAuthOptional } from '../context/AuthContext'
import type { HelpTopic } from '../types/help'

// ─── Hash helpers ──────────────────────────────────────────────────────────────

function parseHash(hash: string): { topicSlug: string; anchor: string } {
  const raw = hash.startsWith('#') ? hash.slice(1) : hash
  const colonIdx = raw.indexOf(':')
  if (colonIdx === -1) return { topicSlug: raw, anchor: '' }
  return { topicSlug: raw.slice(0, colonIdx), anchor: raw.slice(colonIdx + 1) }
}

function buildHash(topicSlug: string, anchor?: string): string {
  return anchor ? `#${topicSlug}:${anchor}` : `#${topicSlug}`
}

// ─── Constants ─────────────────────────────────────────────────────────────────

const LANDING_SLUG = 'usage-index'

// ─── Permission gate (hides admin topics from nav) ─────────────────────────────

function TopicGate({ topic, children }: { topic: HelpTopic; children: React.ReactNode }) {
  const permitted = usePermission(topic.required_permission ?? '')
  if (topic.required_permission && !permitted) return null
  return <>{children}</>
}

// ─── Nav item button ──────────────────────────────────────────────────────────

function NavItem({
  topic,
  isActive,
  onSelect,
}: {
  topic: HelpTopic
  isActive: boolean
  onSelect: () => void
}) {
  return (
    <button
      onClick={onSelect}
      className={clsx(
        'w-full text-left px-3 py-2 rounded text-sm transition-colors',
        isActive
          ? 'bg-accent-soft text-content-selected font-medium'
          : 'text-content-secondary hover:bg-surface-hover hover:text-content-primary',
      )}
    >
      {topic.title}
    </button>
  )
}

// ─── Nav list ─────────────────────────────────────────────────────────────────

function NavList({
  topics,
  activeSlug,
  onSelect,
}: {
  topics: HelpTopic[]
  activeSlug: string
  onSelect: (slug: string) => void
}) {
  // Exclude landing page from nav
  const navTopics = topics.filter((t) => t.slug !== LANDING_SLUG)

  return (
    <>
      <p className="px-3 pb-2 text-xs font-semibold uppercase tracking-wider text-content-muted">
        Help
      </p>
      {navTopics.map((topic) => (
        <TopicGate key={topic.slug} topic={topic}>
          <NavItem
            topic={topic}
            isActive={topic.slug === activeSlug}
            onSelect={() => onSelect(topic.slug)}
          />
        </TopicGate>
      ))}
    </>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function HelpPage() {
  const { hash } = useLocation()
  const navigate = useNavigate()
  const contentRef = useRef<HTMLDivElement>(null)
  const permissions = usePermissions()
  const auth = useAuthOptional()
  const isBootstrapping = auth?.isBootstrapping ?? false

  const { topicSlug: initialSlug } = parseHash(hash)

  // Default to landing page slug when no hash is present
  const [activeSlug, setActiveSlug] = useState<string>(initialSlug || LANDING_SLUG)
  const [mobileNavOpen, setMobileNavOpen] = useState(false)

  const allTopics = helpContent.topics
  const activeTopic = allTopics.find((t) => t.slug === activeSlug) ?? allTopics[0]

  function selectTopic(slug: string, anchor = '') {
    setActiveSlug(slug)
    setMobileNavOpen(false)
    navigate({ hash: buildHash(slug, anchor || undefined) }, { replace: true })
  }

  // Permission gate: redirect to /403 if selected topic requires a permission the user lacks.
  // Wait until auth bootstrapping is complete before enforcing — during bootstrap, permissions
  // are empty and would trigger a false redirect.
  useEffect(() => {
    if (isBootstrapping) return
    if (!activeTopic) return
    if (
      activeTopic.required_permission &&
      !permissions.has(activeTopic.required_permission)
    ) {
      navigate('/403', { replace: true })
    }
  }, [activeTopic, permissions, navigate, isBootstrapping])

  // Scroll to anchor after content renders
  useEffect(() => {
    const { anchor } = parseHash(hash)
    if (!anchor || !contentRef.current) return
    const el = contentRef.current.querySelector(`#${CSS.escape(anchor)}`)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [hash, activeSlug])

  // Sync slug from hash changes (e.g. browser back/forward)
  useEffect(() => {
    const { topicSlug } = parseHash(hash)
    const slug = topicSlug || LANDING_SLUG
    if (slug !== activeSlug) setActiveSlug(slug)
  }, [hash]) // eslint-disable-line react-hooks/exhaustive-deps

  // Make external links open in a new tab
  useEffect(() => {
    if (!contentRef.current) return
    const anchors = contentRef.current.querySelectorAll<HTMLAnchorElement>('a[href]')
    anchors.forEach((a) => {
      const href = a.getAttribute('href') ?? ''
      if (href.startsWith('http://') || href.startsWith('https://')) {
        a.setAttribute('target', '_blank')
        a.setAttribute('rel', 'noopener noreferrer')
      }
    })
  }, [activeTopic])

  if (!activeTopic) {
    return (
      <div className="flex items-center justify-center h-full text-content-muted text-sm">
        No help topics available.
      </div>
    )
  }

  return (
    <div className="flex h-full overflow-hidden relative">
      {/* Mobile nav overlay backdrop */}
      {mobileNavOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-20 md:hidden"
          onClick={() => setMobileNavOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* Left nav — hidden on mobile unless open */}
      <nav
        className={clsx(
          'flex-shrink-0 bg-surface-raised border-r border-border-base overflow-y-auto py-3 px-2',
          // Desktop: always visible
          'md:relative md:block md:w-56',
          // Mobile: overlay panel
          'fixed inset-y-0 left-0 z-30 w-64 transition-transform duration-200 md:transition-none',
          mobileNavOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0',
        )}
        aria-label="Help topics"
      >
        {/* Mobile close button */}
        <div className="flex items-center justify-between mb-1 md:hidden px-1">
          <p className="text-xs font-semibold uppercase tracking-wider text-content-muted">Help</p>
          <button
            onClick={() => setMobileNavOpen(false)}
            className="p-1 rounded text-content-muted hover:text-content-primary transition-colors"
            aria-label="Close navigation"
          >
            <FontAwesomeIcon icon={faXmark} className="w-4 h-4" />
          </button>
        </div>
        {/* Desktop heading */}
        <p className="hidden md:block px-3 pb-2 text-xs font-semibold uppercase tracking-wider text-content-muted">
          Help
        </p>
        {allTopics
          .filter((t) => t.slug !== LANDING_SLUG)
          .map((topic) => (
            <TopicGate key={topic.slug} topic={topic}>
              <NavItem
                topic={topic}
                isActive={topic.slug === activeSlug}
                onSelect={() => selectTopic(topic.slug)}
              />
            </TopicGate>
          ))}
      </nav>

      {/* Content pane */}
      <div className="flex-1 overflow-y-auto min-w-0">
        {/* Toolbar: mobile hamburger + close button */}
        <div className="flex items-center justify-between px-4 pt-4 pb-2 border-b border-border-base">
          <button
            onClick={() => setMobileNavOpen(true)}
            className="flex items-center gap-2 text-sm text-content-secondary hover:text-content-primary transition-colors md:hidden"
            aria-label="Open navigation"
          >
            <FontAwesomeIcon icon={faBars} className="w-4 h-4" />
            <span>Topics</span>
          </button>
          <span className="hidden md:block" />
          <button
            onClick={() => navigate(-1)}
            className="flex items-center gap-1.5 text-xs text-content-muted hover:text-content-primary transition-colors"
            aria-label="Close help"
          >
            <FontAwesomeIcon icon={faArrowLeft} className="w-3 h-3" />
            Back
          </button>
        </div>

        {/* Prose content */}
        <div
          ref={contentRef}
          className={[
            'max-w-3xl px-10 py-8',
            // Scoped prose styles (no @tailwindcss/typography dependency)
            '[&_h1]:text-2xl [&_h1]:font-bold [&_h1]:text-content-primary [&_h1]:mb-4 [&_h1]:mt-0',
            '[&_h2]:text-xl [&_h2]:font-semibold [&_h2]:text-content-primary [&_h2]:mb-3 [&_h2]:mt-8',
            '[&_h3]:text-base [&_h3]:font-semibold [&_h3]:text-content-primary [&_h3]:mb-2 [&_h3]:mt-6',
            '[&_h4]:text-sm [&_h4]:font-semibold [&_h4]:text-content-primary [&_h4]:mb-2 [&_h4]:mt-4',
            '[&_p]:text-sm [&_p]:text-content-secondary [&_p]:leading-relaxed [&_p]:mb-4',
            '[&_ul]:list-disc [&_ul]:pl-5 [&_ul]:mb-4 [&_ul]:text-sm [&_ul]:text-content-secondary',
            '[&_ol]:list-decimal [&_ol]:pl-5 [&_ol]:mb-4 [&_ol]:text-sm [&_ol]:text-content-secondary',
            '[&_li]:mb-1 [&_li]:leading-relaxed',
            '[&_code]:text-xs [&_code]:bg-surface-raised [&_code]:rounded [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-content-primary',
            '[&_pre]:bg-surface-raised [&_pre]:rounded [&_pre]:p-4 [&_pre]:overflow-x-auto [&_pre]:mb-4',
            '[&_pre_code]:bg-transparent [&_pre_code]:p-0',
            '[&_a]:text-accent [&_a]:underline [&_a]:underline-offset-2 [&_a:hover]:opacity-80',
            '[&_table]:w-full [&_table]:text-sm [&_table]:border-collapse [&_table]:mb-4',
            '[&_th]:text-left [&_th]:font-semibold [&_th]:text-content-primary [&_th]:border-b [&_th]:border-border-base [&_th]:pb-2 [&_th]:pr-4',
            '[&_td]:text-content-secondary [&_td]:border-b [&_td]:border-border-base [&_td]:py-2 [&_td]:pr-4',
            '[&_hr]:border-border-base [&_hr]:my-6',
            '[&_blockquote]:border-l-4 [&_blockquote]:border-border-base [&_blockquote]:pl-4 [&_blockquote]:text-content-muted [&_blockquote]:italic [&_blockquote]:mb-4',
            '[&_strong]:font-semibold [&_strong]:text-content-primary',
          ].join(' ')}
          dangerouslySetInnerHTML={{ __html: activeTopic.html }}
        />
      </div>
    </div>
  )
}
