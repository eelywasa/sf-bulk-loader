/**
 * SettingsEmailPage — DB-backed email configuration (SFBL-157).
 *
 * Sections:
 *  - General: backend selector, from address/name, reply-to
 *  - SMTP: shown when email_backend === "smtp"
 *  - SES: shown when email_backend === "ses"
 *  - Advanced: retries, timeouts, misc flags
 *  - Test send: migrated from the old EmailTab; uses existing postEmailTest
 */

import { useState, useMemo } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import { faCircleInfo } from '@fortawesome/free-solid-svg-icons'
import { SettingsPageShell } from './SettingsPageShell'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { LABEL_CLASS, INPUT_CLASS, SELECT_CLASS, ALERT_ERROR, ALERT_SUCCESS, ALERT_WARNING, ALERT_INFO } from '../components/ui/formStyles'
import { dependenciesApi, postEmailTest, EmailRenderFailureError } from '../api/endpoints'
import type { EmailTestTemplate, EmailTestResponse, SettingValue } from '../api/types'
import { ApiError } from '../api/client'
import { useQuery as useRQ } from '@tanstack/react-query'
import { getSettingsCategory } from '../api/endpoints'

// ─── Test send sub-section ─────────────────────────────────────────────────────

const TEMPLATE_OPTIONS: { value: EmailTestTemplate; label: string }[] = [
  { value: 'auth/password_reset', label: 'Password Reset' },
  { value: 'auth/email_change_verify', label: 'Email Change Verification' },
  { value: 'notifications/run_complete', label: 'Run Complete Notification' },
]

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

type SendResult =
  | { kind: 'success'; data: EmailTestResponse }
  | { kind: 'render_failure'; code: string; message: string }
  | { kind: 'http_error'; status: number; message: string }

function SendResultDisplay({ result }: { result: SendResult }) {
  if (result.kind === 'render_failure') {
    return (
      <div className={ALERT_ERROR}>
        <p className="font-semibold mb-1">Template render failed</p>
        <p className="font-mono text-xs">{result.code}</p>
        <p className="mt-1 text-xs opacity-80">{result.message}</p>
      </div>
    )
  }
  if (result.kind === 'http_error') {
    return (
      <div className={ALERT_ERROR}>
        <p className="font-semibold mb-1">Request failed (HTTP {result.status})</p>
        <p className="text-xs opacity-80">{result.message}</p>
      </div>
    )
  }
  const { data } = result
  if (data.status === 'failed') {
    return (
      <div className={`${ALERT_WARNING} space-y-1`}>
        <p className="font-semibold">Backend send failed</p>
        <p className="text-xs"><span className="font-medium">Reason:</span> <span className="font-mono">{data.reason}</span></p>
        {data.last_error_msg && <p className="text-xs"><span className="font-medium">Detail:</span> {data.last_error_msg}</p>}
        <p className="text-xs opacity-70">Delivery ID: {data.delivery_id}</p>
      </div>
    )
  }
  if (data.status === 'pending' || data.status === 'sending') {
    return (
      <div className={`${ALERT_WARNING} space-y-1`}>
        <p className="font-semibold">Send queued for retry</p>
        <p className="text-xs">First attempt failed transiently; a background retry is scheduled.</p>
        <p className="text-xs"><span className="font-medium">Attempts so far:</span> {data.attempts}</p>
        {data.last_error_msg && <p className="text-xs"><span className="font-medium">Detail:</span> {data.last_error_msg}</p>}
        <p className="text-xs opacity-70">Delivery ID: {data.delivery_id}</p>
      </div>
    )
  }
  if (data.status === 'sent' || data.status === 'skipped') {
    return (
      <div className={`${ALERT_SUCCESS} space-y-1`}>
        <p className="font-semibold">
          {data.status === 'skipped'
            ? 'Send skipped (noop backend — no email delivered)'
            : 'Email sent successfully'}
        </p>
        <p className="text-xs opacity-70">Delivery ID: {data.delivery_id}</p>
        {data.provider_message_id && (
          <p className="text-xs opacity-70">Provider message ID: {data.provider_message_id}</p>
        )}
      </div>
    )
  }
  return null
}

function TestSendSection() {
  const [to, setTo] = useState('')
  const [template, setTemplate] = useState<EmailTestTemplate>('auth/password_reset')
  const [sendResult, setSendResult] = useState<SendResult | null>(null)

  const { data: deps } = useQuery({
    queryKey: ['dependencies'],
    queryFn: dependenciesApi.get,
    staleTime: 30_000,
    retry: 1,
  })

  const emailDep = deps?.dependencies?.email
  const emailStatus = emailDep?.status ?? 'ok'
  const emailDetail = emailDep?.detail
  const isValidEmail = EMAIL_RE.test(to.trim())

  const mutation = useMutation({
    mutationFn: () => postEmailTest({ to: to.trim(), template }),
    onSuccess: (data) => setSendResult({ kind: 'success', data }),
    onError: (err) => {
      if (err instanceof EmailRenderFailureError) {
        setSendResult({ kind: 'render_failure', code: err.code, message: err.message })
      } else if (err instanceof ApiError) {
        setSendResult({ kind: 'http_error', status: err.status, message: err.message })
      } else {
        setSendResult({ kind: 'http_error', status: 0, message: err instanceof Error ? err.message : 'Unknown error' })
      }
    },
  })

  function handleSend(e: React.FormEvent) {
    e.preventDefault()
    setSendResult(null)
    mutation.mutate()
  }

  return (
    <div className="space-y-6 mt-8">
      <section className="bg-surface-raised border border-border-base rounded-lg p-6 space-y-4">
        <h2 className="text-sm font-semibold text-content-primary">Email backend status</h2>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
          <div>
            <dt className="text-content-muted text-xs uppercase tracking-wide mb-1">Connection</dt>
            <dd>
              {emailStatus === 'ok' ? (
                <Badge variant="success" dot>healthy</Badge>
              ) : (
                <span className="inline-flex flex-col gap-0.5">
                  <Badge variant="warning" dot>degraded</Badge>
                  {emailDetail && <span className="text-xs text-content-muted">{emailDetail}</span>}
                </span>
              )}
            </dd>
          </div>
        </dl>
      </section>

      <section className="bg-surface-raised border border-border-base rounded-lg p-6 space-y-4">
        <h2 className="text-sm font-semibold text-content-primary">Send test email</h2>
        <p className="text-xs text-content-muted">
          Uses the current <em>saved</em> configuration — not unsaved changes above.
        </p>
        <form onSubmit={handleSend} className="space-y-4">
          <div>
            <label className={LABEL_CLASS} htmlFor="email-to">Recipient</label>
            <input
              id="email-to"
              type="email"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              placeholder="you@example.com"
              className={INPUT_CLASS}
              autoComplete="email"
            />
          </div>
          <div>
            <label className={LABEL_CLASS} htmlFor="email-template">Template</label>
            <select
              id="email-template"
              value={template}
              onChange={(e) => setTemplate(e.target.value as EmailTestTemplate)}
              className={SELECT_CLASS}
            >
              {TEMPLATE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>
          <Button type="submit" disabled={!isValidEmail || mutation.isPending} loading={mutation.isPending}>
            Send test
          </Button>
        </form>
        {sendResult && <div className="mt-4"><SendResultDisplay result={sendResult} /></div>}
      </section>
    </div>
  )
}

// ─── Page ──────────────────────────────────────────────────────────────────────

const SMTP_KEYS = [
  'email_smtp_host',
  'email_smtp_port',
  'email_smtp_username',
  'email_smtp_password',
  'email_smtp_starttls',
  'email_smtp_use_tls',
]

const SES_KEYS = [
  'email_ses_region',
  'email_ses_configuration_set',
]

const GENERAL_KEYS = [
  'email_backend',
  'email_from_address',
  'email_from_name',
  'email_reply_to',
  'frontend_base_url',
]

const ADVANCED_KEYS = [
  'email_max_retries',
  'email_retry_backoff_seconds',
  'email_retry_backoff_max_seconds',
  'email_timeout_seconds',
  'email_claim_lease_seconds',
  'email_pending_stale_minutes',
  'email_log_recipients',
]

export default function SettingsEmailPage() {
  // We need to read email_backend from the live category data so we can show/hide SMTP/SES sections.
  const { data: categoryData } = useRQ({
    queryKey: ['settings', 'email'],
    queryFn: () => getSettingsCategory('email'),
    staleTime: 30_000,
    retry: false,
  })

  const emailBackend = useMemo<string>(() => {
    const setting = categoryData?.data?.settings?.find((s) => s.key === 'email_backend')
    return typeof setting?.value === 'string' ? setting.value : 'noop'
  }, [categoryData])

  const preamble = emailBackend === 'noop' ? (
    <div className={`${ALERT_INFO} mb-6 flex items-start gap-2`}>
      <FontAwesomeIcon icon={faCircleInfo} className="w-4 h-4 flex-shrink-0 mt-0.5" aria-hidden="true" />
      <p>
        Email is disabled. Set <span className="font-mono font-semibold">email_backend</span> to{' '}
        <span className="font-mono">smtp</span> or <span className="font-mono">ses</span> to configure delivery.
      </p>
    </div>
  ) : null

  const sections = useMemo(() => {
    const base = [
      { title: 'General', keys: GENERAL_KEYS },
      { title: 'Advanced', keys: ADVANCED_KEYS },
    ]
    if (emailBackend === 'smtp') {
      base.splice(1, 0, { title: 'SMTP', keys: SMTP_KEYS })
    } else if (emailBackend === 'ses') {
      base.splice(1, 0, { title: 'Amazon SES', keys: SES_KEYS })
    }
    return base
  }, [emailBackend])

  // We need to filter to only include keys that exist in the sections
  const allSectionKeys = sections.flatMap((s) => s.keys)
  const filterSettings = (settings: SettingValue[]) =>
    settings.filter((s) => allSectionKeys.includes(s.key))

  return (
    <SettingsPageShell
      category="email"
      title="Email Settings"
      preamble={preamble}
      footer={<TestSendSection />}
      sections={sections}
      filterSettings={filterSettings}
    />
  )
}
