/**
 * LoginMfaChallenge — phase-2 login view for a user with a TOTP factor.
 *
 * Rendered inline by `Login.tsx` once the phase-1 response returns
 * `mfa_required: true` with `must_enroll: false`. The `mfa_token` is kept in
 * component state only — never persisted to localStorage / URL (spec §10.6).
 *
 * SFBL-204: DOM tab order is `code input → submit → "use backup code" link`.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { ApiError } from '../api/client'
import { loginMfaApi } from '../api/endpoints'
import type { TokenResponse } from '../api/types'
import {
  ALERT_ERROR,
  INPUT_CLASS,
  LABEL_CLASS,
} from '../components/ui/formStyles'
import { BrandMark } from '../components/ui'

export interface LoginMfaChallengeProps {
  mfaToken: string
  nextPath: string
  /**
   * Called when we should bounce back to the Login form — typically after an
   * expired or invalid `mfa_token`. An optional message is propagated so the
   * Login form can surface it as the banner error.
   */
  onAbort: (message?: string) => void
}

export default function LoginMfaChallenge({
  mfaToken,
  nextPath,
  onAbort,
}: LoginMfaChallengeProps) {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [method, setMethod] = useState<'totp' | 'backup_code'>('totp')
  const [code, setCode] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (submitting) return
    setError(null)
    setSubmitting(true)
    try {
      const trimmed = code.trim()
      const resp = await loginMfaApi.verify(mfaToken, {
        method,
        code: trimmed,
      })
      await login((resp as TokenResponse).access_token)
      navigate(nextPath, { replace: true })
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        const code = err.code ?? ''
        if (err.status === 429) {
          setError('Too many attempts — wait a minute and try again.')
        } else if (
          err.status === 401 &&
          (code === 'mfa_token_invalid' ||
            code === 'mfa_token_expired' ||
            /mfa_token/i.test(err.message ?? ''))
        ) {
          onAbort('Session expired, please sign in again.')
        } else if (err.status === 401) {
          setError('Incorrect code — please try again.')
        } else {
          setError('Verification failed. Please try again.')
        }
      } else {
        setError('Verification failed. Please try again.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  function toggleBackup() {
    setMethod((prev) => (prev === 'totp' ? 'backup_code' : 'totp'))
    setCode('')
    setError(null)
  }

  const isBackup = method === 'backup_code'

  return (
    <div className="min-h-screen flex items-center justify-center bg-surface-base">
      <div className="w-full max-w-sm">
        <div className="bg-surface-raised rounded-lg shadow-sm border border-border-base p-8">
          <div className="flex items-center gap-2 mb-6">
            <BrandMark size="md" />
            <span className="text-base font-semibold text-content-primary">
              Bulk Loader
            </span>
          </div>

          <h1 className="text-sm font-medium text-content-secondary mb-1">
            Two-factor verification
          </h1>
          <p className="text-xs text-content-muted mb-5">
            {isBackup
              ? 'Enter one of your one-time backup codes.'
              : 'Enter the 6-digit code from your authenticator app.'}
          </p>

          <form onSubmit={handleSubmit} noValidate>
            {error && (
              <div role="alert" className={`mb-4 ${ALERT_ERROR}`}>
                {error}
              </div>
            )}

            <div className="mb-5">
              <label htmlFor="mfa-challenge-code" className={LABEL_CLASS}>
                {isBackup ? 'Backup code' : 'Authenticator code'}
              </label>
              {isBackup ? (
                <input
                  id="mfa-challenge-code"
                  type="text"
                  inputMode="text"
                  autoComplete="one-time-code"
                  maxLength={12}
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  className={INPUT_CLASS + ' font-mono'}
                  placeholder="xxxxx-xxxxx"
                  autoFocus
                  data-testid="mfa-challenge-input"
                />
              ) : (
                <input
                  id="mfa-challenge-code"
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  autoComplete="one-time-code"
                  maxLength={6}
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                  className={INPUT_CLASS + ' font-mono tracking-widest text-center'}
                  placeholder="123456"
                  autoFocus
                  data-testid="mfa-challenge-input"
                />
              )}
            </div>

            <button
              type="submit"
              disabled={submitting || code.trim().length < (isBackup ? 6 : 6)}
              data-testid="mfa-challenge-submit"
              className="w-full px-4 py-2 text-sm font-medium text-content-inverse bg-accent hover:bg-accent-hover disabled:opacity-50 rounded transition-colors focus:outline-none focus:ring-2 focus:ring-border-focus"
            >
              {submitting ? 'Verifying…' : 'Verify and continue'}
            </button>

            <div className="mt-3 text-right">
              <button
                type="button"
                onClick={toggleBackup}
                data-testid="mfa-challenge-toggle"
                className="text-xs text-accent hover:text-accent-hover"
              >
                {isBackup ? 'Use authenticator app instead' : 'Use a backup code instead'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  )
}
