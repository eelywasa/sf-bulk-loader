/**
 * Email settings tab — admin test-send + backend dependency status.
 *
 * Extracted from pages/Settings.tsx as part of SFBL-183 so the Settings
 * page can host multiple tabs (Email, Notifications).
 */

import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import {
  LABEL_CLASS,
  INPUT_CLASS,
  SELECT_CLASS,
  ALERT_ERROR,
  ALERT_SUCCESS,
  ALERT_WARNING,
} from '../../components/ui/formStyles'
import { dependenciesApi, postEmailTest, EmailRenderFailureError } from '../../api/endpoints'
import type { EmailTestTemplate, EmailTestResponse } from '../../api/types'
import { ApiError } from '../../api/client'

const TEMPLATE_OPTIONS: { value: EmailTestTemplate; label: string }[] = [
  { value: 'auth/password_reset', label: 'Password Reset' },
  { value: 'auth/email_change_verify', label: 'Email Change Verification' },
  { value: 'notifications/run_complete', label: 'Run Complete Notification' },
]

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

function EmailDependencyBadge({
  status,
  detail,
}: {
  status: string
  detail?: string
}) {
  if (status === 'ok') {
    return (
      <Badge variant="success" dot>
        healthy
      </Badge>
    )
  }
  return (
    <span className="inline-flex flex-col gap-0.5">
      <Badge variant="warning" dot>
        degraded
      </Badge>
      {detail && <span className="text-xs text-content-muted">{detail}</span>}
    </span>
  )
}

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
        <p className="text-xs">
          <span className="font-medium">Reason:</span>{' '}
          <span className="font-mono">{data.reason}</span>
        </p>
        {data.last_error_msg && (
          <p className="text-xs">
            <span className="font-medium">Detail:</span> {data.last_error_msg}
          </p>
        )}
        <p className="text-xs opacity-70">Delivery ID: {data.delivery_id}</p>
      </div>
    )
  }

  if (data.status === 'pending' || data.status === 'sending') {
    return (
      <div className={`${ALERT_WARNING} space-y-1`}>
        <p className="font-semibold">Send queued for retry</p>
        <p className="text-xs">
          First attempt failed transiently; a background retry is scheduled.
          Check the email delivery log for the final outcome.
        </p>
        <p className="text-xs">
          <span className="font-medium">Attempts so far:</span> {data.attempts}
        </p>
        {data.reason && (
          <p className="text-xs">
            <span className="font-medium">Reason:</span>{' '}
            <span className="font-mono">{data.reason}</span>
          </p>
        )}
        {data.last_error_msg && (
          <p className="text-xs">
            <span className="font-medium">Detail:</span> {data.last_error_msg}
          </p>
        )}
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

export function EmailTab() {
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
        setSendResult({
          kind: 'http_error',
          status: 0,
          message: err instanceof Error ? err.message : 'Unknown error',
        })
      }
    },
  })

  function handleSend(e: React.FormEvent) {
    e.preventDefault()
    setSendResult(null)
    mutation.mutate()
  }

  return (
    <div className="space-y-6">
      <section className="bg-surface-raised border border-border-base rounded-lg p-6 space-y-4">
        <h2 className="text-sm font-semibold text-content-primary">Email backend status</h2>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
          <div>
            <dt className="text-content-muted text-xs uppercase tracking-wide mb-1">
              Connection
            </dt>
            <dd>
              <EmailDependencyBadge status={emailStatus} detail={emailDetail} />
            </dd>
          </div>
        </dl>
      </section>

      <section className="bg-surface-raised border border-border-base rounded-lg p-6 space-y-4">
        <h2 className="text-sm font-semibold text-content-primary">Send test email</h2>
        <form onSubmit={handleSend} className="space-y-4">
          <div>
            <label className={LABEL_CLASS} htmlFor="email-to">
              Recipient
            </label>
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
            <label className={LABEL_CLASS} htmlFor="email-template">
              Template
            </label>
            <select
              id="email-template"
              value={template}
              onChange={(e) => setTemplate(e.target.value as EmailTestTemplate)}
              className={SELECT_CLASS}
            >
              {TEMPLATE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>
          <Button
            type="submit"
            disabled={!isValidEmail || mutation.isPending}
            loading={mutation.isPending}
          >
            Send test
          </Button>
        </form>

        {sendResult && (
          <div className="mt-4">
            <SendResultDisplay result={sendResult} />
          </div>
        )}
      </section>
    </div>
  )
}
