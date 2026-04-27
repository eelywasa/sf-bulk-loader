/**
 * Public invitation-accept page — /invite/accept?token=<raw_token>
 *
 * On mount: calls GET /api/invitations/{token} to validate the token and
 * show the invitee's email + a welcome message.
 *
 * Password form: calls POST /api/invitations/{token}/accept, stores the
 * returned JWT via AuthContext.login(), then redirects to /.
 *
 * Route: /invite/accept (PUBLIC — outside ProtectedRoute)
 * SFBL-202
 */

import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { ApiError } from '../api/client'
import { invitationsApi } from '../api/endpoints'
import type { InvitationInfo } from '../api/types'
import {
  LABEL_CLASS,
  INPUT_CLASS,
  ALERT_ERROR,
} from '../components/ui/formStyles'
import { BrandMark } from '../components/ui'

// ─── Password strength meter ─────────────────────────────────────────────────

function StrengthHint({ password }: { password: string }) {
  const checks = [
    { label: 'At least 12 characters', met: password.length >= 12 },
    { label: 'Uppercase and lowercase', met: /[a-z]/.test(password) && /[A-Z]/.test(password) },
    { label: 'Contains a digit', met: /\d/.test(password) },
    { label: 'Contains a symbol', met: /[^a-zA-Z0-9]/.test(password) },
  ]

  if (!password) return null

  return (
    <ul className="mt-2 space-y-0.5">
      {checks.map(({ label, met }) => (
        <li key={label} className={`text-xs ${met ? 'text-success-text' : 'text-content-muted'}`}>
          {met ? '✓' : '○'} {label}
        </li>
      ))}
    </ul>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

type PageState = 'loading' | 'form' | 'invalid'

export default function InviteAcceptPage() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const { login } = useAuth()

  const token = searchParams.get('token') ?? ''

  const [pageState, setPageState] = useState<PageState>('loading')
  const [inviteInfo, setInviteInfo] = useState<InvitationInfo | null>(null)
  const [invalidReason, setInvalidReason] = useState<string>('This invitation link is invalid or has expired.')

  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const passwordRef = useRef<HTMLInputElement>(null)

  // Validate token on mount
  useEffect(() => {
    if (!token) {
      setInvalidReason('No invitation token was found in this link.')
      setPageState('invalid')
      return
    }

    invitationsApi.getInfo(token)
      .then((info) => {
        setInviteInfo(info)
        setPageState('form')
        setTimeout(() => passwordRef.current?.focus(), 50)
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 410) {
          setInvalidReason('This invitation has already been accepted.')
        } else {
          setInvalidReason('This invitation link is invalid or has expired.')
        }
        setPageState('invalid')
      })
  }, [token])

  const passwordMismatch = confirmPassword.length > 0 && password !== confirmPassword

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (passwordMismatch || !token) return

    setError(null)
    setLoading(true)
    try {
      const data = await invitationsApi.accept(token, { password })
      await login(data.access_token)
      navigate('/', { replace: true })
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        if (err.status === 410) {
          setError('This invitation has already been accepted or has expired. Please contact your administrator.')
        } else if (err.status === 422) {
          // Password policy failure — extract structured failures if available.
          // The backend returns detail = { error, message, failures: [...] } on this path.
          const detail = err.detail
          const failures =
            detail && typeof detail === 'object' && !Array.isArray(detail) &&
            Array.isArray((detail as { failures?: unknown }).failures)
              ? ((detail as { failures: unknown[] }).failures.filter(
                  (f): f is string => typeof f === 'string',
                ))
              : []
          if (failures.length > 0) {
            setError(`Password requirements not met: ${failures.join(', ')}.`)
          } else {
            setError('Password does not meet the security requirements.')
          }
        } else {
          setError('Something went wrong. Please try again.')
        }
      } else {
        setError('Something went wrong. Please try again.')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-dvh grid place-items-center bg-surface-base">
      <div className="w-full max-w-md px-4">
        <div className="bg-surface-raised rounded-lg shadow-sm border border-border-base p-8">
          {/* Logo */}
          <div className="flex items-center gap-2 mb-6">
            <BrandMark size="md" />
            <span className="text-base font-semibold text-content-primary">
              Bulk Loader
            </span>
          </div>

          {/* Loading state */}
          {pageState === 'loading' && (
            <p className="text-sm text-content-muted">Validating your invitation…</p>
          )}

          {/* Invalid / expired */}
          {pageState === 'invalid' && (
            <>
              <h1 className="text-sm font-semibold text-content-primary mb-3">
                Invitation unavailable
              </h1>
              <div role="alert" className={ALERT_ERROR}>
                {invalidReason}
              </div>
              <p className="mt-4 text-xs text-content-muted">
                Contact your administrator to request a new invitation.
              </p>
            </>
          )}

          {/* Accept form */}
          {pageState === 'form' && inviteInfo && (
            <>
              <h1 className="text-sm font-semibold text-content-primary mb-1">
                Welcome to Bulk Loader
              </h1>
              <p className="text-xs text-content-muted mb-5">
                You've been invited as <span className="font-medium text-content-primary">{inviteInfo.email}</span>
                {inviteInfo.profile_name ? (
                  <> ({inviteInfo.profile_name})</>
                ) : null}
                . Set a password to activate your account.
              </p>

              <form onSubmit={handleSubmit} noValidate>
                {error && (
                  <div role="alert" className={`mb-4 ${ALERT_ERROR}`}>
                    {error}
                  </div>
                )}

                <div className="mb-4">
                  <label htmlFor="password" className={LABEL_CLASS}>
                    Password
                  </label>
                  <input
                    id="password"
                    ref={passwordRef}
                    type="password"
                    autoComplete="new-password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className={INPUT_CLASS}
                  />
                  <StrengthHint password={password} />
                </div>

                <div className="mb-5">
                  <label htmlFor="confirm-password" className={LABEL_CLASS}>
                    Confirm password
                  </label>
                  <input
                    id="confirm-password"
                    type="password"
                    autoComplete="new-password"
                    required
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    className={`${INPUT_CLASS}${passwordMismatch ? ' border-error-border focus:ring-error-border' : ''}`}
                  />
                  {passwordMismatch && (
                    <p className="mt-1 text-xs text-error-text">Passwords do not match</p>
                  )}
                </div>

                <button
                  type="submit"
                  disabled={loading || passwordMismatch || !password}
                  className="w-full px-4 py-2 text-sm font-medium text-content-inverse bg-accent hover:bg-accent-hover disabled:opacity-50 rounded transition-colors focus:outline-none focus:ring-2 focus:ring-border-focus"
                >
                  {loading ? 'Activating account…' : 'Set password and sign in'}
                </button>
              </form>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
