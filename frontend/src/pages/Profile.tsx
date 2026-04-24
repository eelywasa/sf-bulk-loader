/**
 * Profile page — lets authenticated users update their display name,
 * request an email address change, and change their password.
 *
 * Route: /profile (protected)
 * SFBL-149 + SFBL-192
 */

import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useAuth } from '../context/AuthContext'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Modal } from '../components/ui/Modal'
import {
  LABEL_CLASS,
  INPUT_CLASS,
  ALERT_ERROR,
  ALERT_SUCCESS,
  ALERT_WARNING,
  ALERT_INFO,
} from '../components/ui/formStyles'
import { meApi, mfaApi } from '../api/endpoints'
import { ApiError } from '../api/client'
import type { LoginHistoryEntry } from '../api/types'
import MfaEnrollWizard from './MfaEnrollWizard'
import MfaBackupCodesModal from './MfaBackupCodesModal'

// ─── Helpers ─────────────────────────────────────────────────────────────────

function extractMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return 'An unexpected error occurred'
}

function passwordStrengthHint(pwd: string): string | null {
  const issues: string[] = []
  if (pwd.length < 12) issues.push('at least 12 characters')
  if (!/[a-z]/.test(pwd) || !/[A-Z]/.test(pwd)) issues.push('mixed case')
  if (!/\d/.test(pwd)) issues.push('a digit')
  if (!/[^a-zA-Z0-9]/.test(pwd)) issues.push('a symbol')
  return issues.length === 0 ? null : `Password needs: ${issues.join(', ')}.`
}

// ─── Card: Account identity ──────────────────────────────────────────────────

function IdentityCard() {
  const { user } = useAuth()

  return (
    <section className="bg-surface-raised border border-border-base rounded-lg p-6 space-y-4">
      <h2 className="text-sm font-semibold text-content-primary">Account identity</h2>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
        <div>
          <dt className="text-content-muted text-xs uppercase tracking-wide mb-1">Email</dt>
          <dd className="text-content-primary font-mono">{user?.email ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-content-muted text-xs uppercase tracking-wide mb-1">Role</dt>
          <dd>
            <Badge variant="neutral">{user?.profile?.name ?? '—'}</Badge>
          </dd>
        </div>
        <div>
          <dt className="text-content-muted text-xs uppercase tracking-wide mb-1">Status</dt>
          <dd>
            {user?.is_active ? (
              <Badge variant="success" dot>active</Badge>
            ) : (
              <Badge variant="warning" dot>inactive</Badge>
            )}
          </dd>
        </div>
      </dl>
    </section>
  )
}

// ─── Card: Display name ──────────────────────────────────────────────────────

function DisplayNameCard() {
  const { user, login } = useAuth()
  const [displayName, setDisplayName] = useState(user?.display_name ?? '')
  const [alert, setAlert] = useState<{ kind: 'success' | 'error'; message: string } | null>(null)

  const original = user?.display_name ?? ''
  const isDirty = displayName !== original
  const canSave = isDirty && displayName.trim().length > 0

  const mutation = useMutation({
    mutationFn: () => meApi.updateProfile({ display_name: displayName.trim() }),
    onSuccess: async () => {
      // Re-fetch the user via login with the current stored token
      const { getStoredToken } = await import('../api/client')
      const tok = getStoredToken()
      if (tok) await login(tok)
      setAlert({ kind: 'success', message: 'Display name updated.' })
    },
    onError: (err) => {
      setAlert({ kind: 'error', message: extractMessage(err) })
    },
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setAlert(null)
    mutation.mutate()
  }

  return (
    <section className="bg-surface-raised border border-border-base rounded-lg p-6 space-y-4">
      <h2 className="text-sm font-semibold text-content-primary">Display name</h2>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className={LABEL_CLASS} htmlFor="display-name">
            Display name
          </label>
          <input
            id="display-name"
            type="text"
            value={displayName}
            onChange={(e) => { setDisplayName(e.target.value); setAlert(null) }}
            placeholder="Your display name"
            className={INPUT_CLASS}
            autoComplete="name"
          />
        </div>
        <Button
          type="submit"
          disabled={!canSave || mutation.isPending}
          loading={mutation.isPending}
        >
          Save
        </Button>
      </form>
      {alert && (
        <div className={alert.kind === 'success' ? ALERT_SUCCESS : ALERT_ERROR} role="alert">
          {alert.message}
        </div>
      )}
    </section>
  )
}

// ─── Card: Email address ──────────────────────────────────────────────────────

function EmailCard() {
  const { user } = useAuth()
  const [newEmail, setNewEmail] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [alert, setAlert] = useState<{ kind: 'success' | 'error' | 'warning'; message: string } | null>(null)

  const mutation = useMutation({
    mutationFn: () => meApi.requestEmailChange({ new_email: newEmail.trim() }),
    onSuccess: () => {
      setConfirmed(true)
      setAlert(null)
    },
    onError: (err) => {
      if (err instanceof ApiError) {
        if (err.status === 429) {
          setAlert({ kind: 'warning', message: 'Too many requests. Please wait before trying again.' })
        } else {
          setAlert({ kind: 'error', message: err.message })
        }
      } else {
        setAlert({ kind: 'error', message: extractMessage(err) })
      }
    },
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setAlert(null)
    mutation.mutate()
  }

  return (
    <section className="bg-surface-raised border border-border-base rounded-lg p-6 space-y-4">
      <h2 className="text-sm font-semibold text-content-primary">Email address</h2>

      <p className="text-sm text-content-secondary">
        Current email:{' '}
        <span className="font-medium text-content-primary">
          {user?.email ?? 'No email on file'}
        </span>
      </p>

      {confirmed ? (
        <div className={ALERT_SUCCESS} role="status">
          Check your inbox — a verification link will arrive within 30 minutes.
          Click it to confirm your new address.
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className={LABEL_CLASS} htmlFor="new-email">
              New email address
            </label>
            <input
              id="new-email"
              type="email"
              value={newEmail}
              onChange={(e) => { setNewEmail(e.target.value); setAlert(null) }}
              placeholder="new@example.com"
              className={INPUT_CLASS}
              autoComplete="email"
            />
          </div>
          <Button
            type="submit"
            disabled={!newEmail.trim() || mutation.isPending}
            loading={mutation.isPending}
          >
            Request change
          </Button>
          {alert && (
            <div
              className={
                alert.kind === 'warning'
                  ? ALERT_WARNING
                  : alert.kind === 'success'
                  ? ALERT_SUCCESS
                  : ALERT_ERROR
              }
              role="alert"
            >
              {alert.message}
            </div>
          )}
        </form>
      )}
    </section>
  )
}

// ─── Card: Password ───────────────────────────────────────────────────────────

function PasswordCard() {
  const { login } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [alert, setAlert] = useState<{ kind: 'success' | 'error'; message: string } | null>(null)

  const mismatch = confirm.length > 0 && next !== confirm
  const hint = next.length > 0 ? passwordStrengthHint(next) : null
  const canSave = current.length > 0 && next.length > 0 && next === confirm && !hint

  const mutation = useMutation({
    mutationFn: () =>
      meApi.changePassword({ current_password: current, new_password: next }),
    onSuccess: async (data) => {
      await login(data.access_token)
      setAlert({ kind: 'success', message: 'Password changed successfully. You are still signed in.' })
      setCurrent('')
      setNext('')
      setConfirm('')
    },
    onError: (err) => {
      setAlert({ kind: 'error', message: extractMessage(err) })
    },
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setAlert(null)
    mutation.mutate()
  }

  return (
    <section className="bg-surface-raised border border-border-base rounded-lg p-6 space-y-4">
      <h2 className="text-sm font-semibold text-content-primary">Password</h2>
      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className={LABEL_CLASS} htmlFor="current-password">
            Current password
          </label>
          <input
            id="current-password"
            type="password"
            value={current}
            onChange={(e) => { setCurrent(e.target.value); setAlert(null) }}
            className={INPUT_CLASS}
            autoComplete="current-password"
          />
        </div>
        <div>
          <label className={LABEL_CLASS} htmlFor="new-password">
            New password
          </label>
          <input
            id="new-password"
            type="password"
            value={next}
            onChange={(e) => { setNext(e.target.value); setAlert(null) }}
            className={INPUT_CLASS}
            autoComplete="new-password"
          />
          {hint && (
            <p className="mt-1 text-xs text-content-muted">{hint}</p>
          )}
        </div>
        <div>
          <label className={LABEL_CLASS} htmlFor="confirm-password">
            Confirm new password
          </label>
          <input
            id="confirm-password"
            type="password"
            value={confirm}
            onChange={(e) => { setConfirm(e.target.value); setAlert(null) }}
            className={INPUT_CLASS}
            autoComplete="new-password"
          />
          {mismatch && (
            <p className="mt-1 text-xs text-content-muted" role="alert">Passwords do not match.</p>
          )}
        </div>
        <Button
          type="submit"
          disabled={!canSave || mutation.isPending}
          loading={mutation.isPending}
        >
          Change password
        </Button>
      </form>
      {alert && (
        <div className={alert.kind === 'success' ? ALERT_SUCCESS : ALERT_ERROR} role="alert">
          {alert.message}
        </div>
      )}
    </section>
  )
}

// ─── Card: Security (2FA) ────────────────────────────────────────────────────

function DisableMfaModal({ open, onClose, onDisabled }: { open: boolean; onClose: () => void; onDisabled: () => void }) {
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await mfaApi.disable({ password, code: code.trim() })
      onDisabled()
    } catch (err) {
      setError(extractMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Disable two-factor authentication"
      description="Confirm your password and a current 6-digit code to remove 2FA from your account."
      size="md"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button
            type="submit"
            form="mfa-disable-form"
            variant="danger"
            loading={submitting}
            disabled={submitting || password.length === 0 || code.trim().length < 6}
          >
            Disable 2FA
          </Button>
        </>
      }
    >
      <form id="mfa-disable-form" onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className={LABEL_CLASS} htmlFor="disable-password">Current password</label>
          <input
            id="disable-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => { setPassword(e.target.value); setError(null) }}
            className={INPUT_CLASS}
          />
        </div>
        <div>
          <label className={LABEL_CLASS} htmlFor="disable-code">Authenticator code</label>
          <input
            id="disable-code"
            type="text"
            inputMode="numeric"
            pattern="[0-9]*"
            autoComplete="one-time-code"
            maxLength={6}
            value={code}
            onChange={(e) => { setCode(e.target.value.replace(/\D/g, '')); setError(null) }}
            className={INPUT_CLASS + ' font-mono tracking-widest'}
            placeholder="123456"
          />
        </div>
        {error && (
          <div className={ALERT_ERROR} role="alert">{error}</div>
        )}
      </form>
    </Modal>
  )
}

function RegenerateBackupCodesModal({ open, onClose, onRegenerated }: { open: boolean; onClose: () => void; onRegenerated: (codes: string[]) => void }) {
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const res = await mfaApi.regenerateBackupCodes({ code: code.trim() })
      onRegenerated(res.backup_codes)
    } catch (err) {
      setError(extractMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Regenerate backup codes"
      description="Enter a current 6-digit code to replace your backup-code set. Any existing backup codes will be invalidated."
      size="md"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button
            type="submit"
            form="mfa-regen-form"
            variant="primary"
            loading={submitting}
            disabled={submitting || code.trim().length < 6}
          >
            Regenerate
          </Button>
        </>
      }
    >
      <form id="mfa-regen-form" onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className={LABEL_CLASS} htmlFor="regen-code">Authenticator code</label>
          <input
            id="regen-code"
            type="text"
            inputMode="numeric"
            pattern="[0-9]*"
            autoComplete="one-time-code"
            maxLength={6}
            value={code}
            onChange={(e) => { setCode(e.target.value.replace(/\D/g, '')); setError(null) }}
            className={INPUT_CLASS + ' font-mono tracking-widest'}
            placeholder="123456"
            autoFocus
          />
        </div>
        {error && (
          <div className={ALERT_ERROR} role="alert">{error}</div>
        )}
      </form>
    </Modal>
  )
}

function SecurityCard() {
  const { user, login } = useAuth()
  const [wizardOpen, setWizardOpen] = useState(false)
  const [disableOpen, setDisableOpen] = useState(false)
  const [regenOpen, setRegenOpen] = useState(false)
  const [regenCodes, setRegenCodes] = useState<string[] | null>(null)
  const [alert, setAlert] = useState<{ kind: 'success' | 'error'; message: string } | null>(null)

  const mfa = user?.mfa
  const enrolled = Boolean(mfa?.enrolled)
  const tenantRequired = Boolean(mfa?.tenant_required)

  async function refreshMe() {
    const { getStoredToken } = await import('../api/client')
    const tok = getStoredToken()
    if (tok) await login(tok)
  }

  return (
    <section
      className="bg-surface-raised border border-border-base rounded-lg p-6 space-y-4"
      data-testid="security-card"
    >
      <h2 className="text-sm font-semibold text-content-primary">Security</h2>

      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="space-y-1">
          <div className="text-sm text-content-primary">
            Two-factor authentication:{' '}
            {enrolled ? (
              <Badge variant="success" dot>On</Badge>
            ) : (
              <Badge variant="neutral">Off</Badge>
            )}
          </div>
          {enrolled && mfa && (
            <p className="text-xs text-content-muted" data-testid="backup-codes-remaining">
              {mfa.backup_codes_remaining} backup {mfa.backup_codes_remaining === 1 ? 'code' : 'codes'} remaining
            </p>
          )}
          {!enrolled && (
            <p className="text-xs text-content-muted">
              Add a 6-digit code from an authenticator app at sign-in.
            </p>
          )}
        </div>

        <div className="flex gap-2 flex-wrap">
          {!enrolled && (
            <Button variant="primary" onClick={() => setWizardOpen(true)} data-testid="mfa-setup">
              Set up
            </Button>
          )}
          {enrolled && (
            <>
              <Button variant="secondary" onClick={() => setRegenOpen(true)} data-testid="mfa-regen">
                Regenerate backup codes
              </Button>
              {!tenantRequired && (
                <Button variant="danger" onClick={() => setDisableOpen(true)} data-testid="mfa-disable">
                  Disable 2FA
                </Button>
              )}
            </>
          )}
        </div>
      </div>

      {enrolled && mfa && mfa.backup_codes_remaining <= 2 && (
        <div className={ALERT_INFO}>
          You&rsquo;re running low on backup codes. Regenerate a new set before you lose your authenticator.
        </div>
      )}

      {alert && (
        <div className={alert.kind === 'success' ? ALERT_SUCCESS : ALERT_ERROR} role="alert">
          {alert.message}
        </div>
      )}

      <MfaEnrollWizard
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        onEnrolled={() => {
          setAlert({ kind: 'success', message: 'Two-factor authentication is now on.' })
          // token was refreshed inside the wizard; user object already updated
        }}
      />

      <DisableMfaModal
        open={disableOpen}
        onClose={() => setDisableOpen(false)}
        onDisabled={async () => {
          setDisableOpen(false)
          await refreshMe()
          setAlert({ kind: 'success', message: 'Two-factor authentication disabled.' })
        }}
      />

      <RegenerateBackupCodesModal
        open={regenOpen}
        onClose={() => setRegenOpen(false)}
        onRegenerated={async (codes) => {
          setRegenOpen(false)
          setRegenCodes(codes)
          await refreshMe()
        }}
      />

      {regenCodes && (
        <MfaBackupCodesModal
          codes={regenCodes}
          onClose={() => {
            setRegenCodes(null)
            setAlert({ kind: 'success', message: 'Backup codes regenerated.' })
          }}
        />
      )}
    </section>
  )
}

// ─── Helpers: date formatting ────────────────────────────────────────────────

/**
 * Returns a relative time string (e.g. "3 minutes ago") using the
 * Intl.RelativeTimeFormat API, falling back to the absolute ISO string
 * if the browser doesn't support it.
 */
function relativeTime(isoString: string): string {
  try {
    const date = new Date(isoString)
    const diffMs = date.getTime() - Date.now()
    const diffSec = Math.round(diffMs / 1000)
    const rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })

    const thresholds: [number, Intl.RelativeTimeFormatUnit][] = [
      [60, 'second'],
      [3600, 'minute'],
      [86400, 'hour'],
      [86400 * 30, 'day'],
      [86400 * 365, 'month'],
      [Infinity, 'year'],
    ]

    let divisor = 1
    let unit: Intl.RelativeTimeFormatUnit = 'second'
    for (const [limit, u] of thresholds) {
      unit = u
      if (Math.abs(diffSec) < limit) break
      divisor = limit
    }
    return rtf.format(Math.round(diffSec / divisor), unit)
  } catch {
    return isoString
  }
}

function absoluteTime(isoString: string): string {
  try {
    return new Date(isoString).toLocaleString()
  } catch {
    return isoString
  }
}

// ─── Card: Recent sign-in activity ──────────────────────────────────────────

function LoginHistoryCard() {
  const { data: entries, isLoading, isError } = useQuery<LoginHistoryEntry[]>({
    queryKey: ['me', 'login-history'],
    queryFn: () => meApi.getLoginHistory(10),
    staleTime: 30_000,
    retry: 1,
  })

  return (
    <section
      className="bg-surface-raised border border-border-base rounded-lg p-6 space-y-4"
      data-testid="login-history-card"
    >
      <h2 className="text-sm font-semibold text-content-primary">Recent sign-in activity</h2>

      {isLoading && (
        <p className="text-sm text-content-muted">Loading…</p>
      )}

      {isError && (
        <p className="text-sm text-content-muted">Could not load sign-in history.</p>
      )}

      {!isLoading && !isError && entries && entries.length === 0 && (
        <p className="text-sm text-content-muted" data-testid="login-history-empty">
          No sign-in activity recorded yet.
        </p>
      )}

      {!isLoading && !isError && entries && entries.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" data-testid="login-history-table">
            <thead>
              <tr className="border-b border-border-base text-left">
                <th className="pb-2 text-xs font-medium text-content-muted uppercase tracking-wide pr-6">
                  Time
                </th>
                <th className="pb-2 text-xs font-medium text-content-muted uppercase tracking-wide pr-6">
                  IP
                </th>
                <th className="pb-2 text-xs font-medium text-content-muted uppercase tracking-wide">
                  Result
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-base">
              {entries.map((entry, idx) => (
                <tr key={idx} className="py-2">
                  <td className="py-2 pr-6 text-content-secondary whitespace-nowrap">
                    <span
                      title={absoluteTime(entry.attempted_at)}
                      className="cursor-default"
                    >
                      {relativeTime(entry.attempted_at)}
                    </span>
                  </td>
                  <td className="py-2 pr-6 font-mono text-content-primary">
                    {entry.ip}
                  </td>
                  <td className="py-2">
                    {entry.outcome === 'Success' ? (
                      <Badge variant="success">{entry.outcome}</Badge>
                    ) : (
                      <Badge variant="warning">{entry.outcome}</Badge>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

// ─── Profile page ─────────────────────────────────────────────────────────────

export default function Profile() {
  return (
    <div className="p-6 max-w-2xl">
      <h1 className="text-xl font-semibold text-content-primary mb-6">Profile</h1>
      <div className="space-y-6">
        <IdentityCard />
        <DisplayNameCard />
        <EmailCard />
        <PasswordCard />
        <SecurityCard />
        <LoginHistoryCard />
      </div>
    </div>
  )
}
