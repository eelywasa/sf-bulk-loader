/**
 * Profile page — lets authenticated users update their display name,
 * request an email address change, and change their password.
 *
 * Route: /profile (protected)
 * SFBL-149
 */

import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useAuth } from '../context/AuthContext'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import {
  LABEL_CLASS,
  INPUT_CLASS,
  ALERT_ERROR,
  ALERT_SUCCESS,
  ALERT_WARNING,
} from '../components/ui/formStyles'
import { meApi } from '../api/endpoints'
import { ApiError } from '../api/client'

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
          <dt className="text-content-muted text-xs uppercase tracking-wide mb-1">Username</dt>
          <dd className="text-content-primary font-mono">{user?.username ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-content-muted text-xs uppercase tracking-wide mb-1">Role</dt>
          <dd>
            <Badge variant="neutral">{user?.role ?? '—'}</Badge>
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
      </div>
    </div>
  )
}
