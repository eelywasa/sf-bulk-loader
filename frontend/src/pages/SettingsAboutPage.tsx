import { useQuery } from '@tanstack/react-query'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import { faCopy, faCheck } from '@fortawesome/free-solid-svg-icons'
import { useState } from 'react'
import { getAbout } from '../api/endpoints'
import type { AboutPayload } from '../api/types'
import { BUTTON_SECONDARY_CLASS } from '../components/ui/formStyles'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function val(v: string | number | boolean | null | undefined): string {
  if (v === null || v === undefined || v === '' || v === 'unknown') return '—'
  return String(v)
}

function providerCounts(counts: Record<string, number>): string {
  const entries = Object.entries(counts)
  if (entries.length === 0) return '—'
  return entries.map(([k, n]) => `${k}: ${n}`).join(', ')
}

// ─── Card / row primitives ────────────────────────────────────────────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-surface-raised border border-border-base rounded-lg overflow-hidden">
      <div className="px-4 py-3 border-b border-border-base bg-surface-elevated">
        <h2 className="text-sm font-semibold text-content-primary">{title}</h2>
      </div>
      <div className="divide-y divide-border-base">{children}</div>
    </div>
  )
}

function Row({ label, value, empty }: { label: string; value: string; empty?: boolean }) {
  return (
    <div className="flex items-baseline px-4 py-2.5 gap-4">
      <span className="w-40 flex-shrink-0 text-sm font-medium text-content-secondary">{label}</span>
      <span
        className={
          empty || value === '—'
            ? 'text-sm text-content-disabled font-mono'
            : 'text-sm text-content-primary font-mono'
        }
      >
        {value}
      </span>
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function SettingsAboutPage() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['admin-about'],
    queryFn: getAbout,
    staleTime: 30_000,
  })

  const [copied, setCopied] = useState(false)

  function handleCopy() {
    if (!data) return
    navigator.clipboard.writeText(JSON.stringify(data, null, 2)).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div className="p-6 max-w-3xl space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-content-primary">About</h1>
          <p className="mt-1 text-sm text-content-secondary">
            System information for this deployment. Paste the JSON into bug reports.
          </p>
        </div>
        <button
          onClick={handleCopy}
          disabled={!data}
          className={BUTTON_SECONDARY_CLASS + ' flex items-center gap-2 text-sm'}
          title="Copy full payload as JSON"
        >
          <FontAwesomeIcon icon={copied ? faCheck : faCopy} className="w-3.5 h-3.5" />
          {copied ? 'Copied' : 'Copy as JSON'}
        </button>
      </div>

      {isLoading && (
        <p className="text-sm text-content-secondary py-8 text-center">Loading…</p>
      )}

      {isError && (
        <div className="bg-feedback-error-subtle border border-feedback-error rounded-lg px-4 py-3 text-sm text-feedback-error">
          Failed to load system info.{' '}
          {error instanceof Error ? error.message : 'Unknown error.'}
        </div>
      )}

      {data && <AboutSections data={data} />}
    </div>
  )
}

function AboutSections({ data }: { data: AboutPayload }) {
  return (
    <div className="space-y-4">
      <Section title="App">
        <Row label="Version" value={val(data.app.version)} />
        <Row label="Git SHA" value={val(data.app.git_sha)} />
        <Row label="Build time" value={val(data.app.build_time)} />
      </Section>

      <Section title="Distribution">
        <Row label="Profile" value={val(data.distribution.profile)} />
        <Row label="Auth mode" value={val(data.distribution.auth_mode)} />
      </Section>

      <Section title="Runtime">
        <Row label="Python" value={val(data.runtime.python_version)} />
        <Row label="FastAPI" value={val(data.runtime.fastapi_version)} />
      </Section>

      <Section title="Database">
        <Row label="Backend" value={val(data.database.backend)} />
        <Row label="Alembic head" value={val(data.database.alembic_head)} />
      </Section>

      <Section title="Salesforce">
        <Row label="API version" value={val(data.salesforce.api_version)} />
      </Section>

      <Section title="Email &amp; Storage">
        <Row label="Email backend" value={val(data.email.backend)} />
        <Row label="Email enabled" value={data.email.enabled ? 'yes' : 'no'} />
        <Row label="Input connections" value={providerCounts(data.storage.input_connections)} />
        <Row label="Output connections" value={providerCounts(data.storage.output_connections)} />
      </Section>
    </div>
  )
}
