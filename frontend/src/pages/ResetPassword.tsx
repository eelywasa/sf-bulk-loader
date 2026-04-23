import { useState, useRef, useEffect } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import { confirmPasswordReset } from '../api/endpoints'
import { LABEL_CLASS, INPUT_CLASS, ALERT_ERROR, ALERT_SUCCESS } from '../components/ui/formStyles'
import { BrandMark } from '../components/ui'

type PageState = 'form' | 'success'

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

export default function ResetPassword() {
  const { token } = useParams<{ token: string }>()
  const [pageState, setPageState] = useState<PageState>('form')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const passwordRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    passwordRef.current?.focus()
  }, [])

  const passwordMismatch = confirmPassword.length > 0 && newPassword !== confirmPassword

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (passwordMismatch) return
    if (!token) {
      setError('Reset token is missing. Please use the link from your email.')
      return
    }
    setError(null)
    setLoading(true)
    try {
      await confirmPasswordReset({ token, new_password: newPassword })
      setPageState('success')
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setError(err.message || 'Password reset failed. The link may be invalid or expired.')
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
          <div className="flex items-center gap-2 mb-6">
            <BrandMark size="md" />
            <span className="text-base font-semibold text-content-primary">
              Bulk Loader
            </span>
          </div>

          {pageState === 'form' ? (
            <>
              <h1 className="text-sm font-medium text-content-secondary mb-5">
                Set a new password
              </h1>

              <form onSubmit={handleSubmit} noValidate>
                {error && (
                  <div role="alert" className={`mb-4 ${ALERT_ERROR}`}>
                    {error}
                  </div>
                )}

                <div className="mb-4">
                  <label htmlFor="new-password" className={LABEL_CLASS}>
                    New password
                  </label>
                  <input
                    id="new-password"
                    ref={passwordRef}
                    type="password"
                    autoComplete="new-password"
                    required
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    className={INPUT_CLASS}
                  />
                  <StrengthHint password={newPassword} />
                </div>

                <div className="mb-5">
                  <label htmlFor="confirm-password" className={LABEL_CLASS}>
                    Confirm new password
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
                  disabled={loading || passwordMismatch}
                  className="w-full px-4 py-2 text-sm font-medium text-content-inverse bg-accent hover:bg-accent-hover disabled:opacity-50 rounded transition-colors focus:outline-none focus:ring-2 focus:ring-border-focus"
                >
                  {loading ? 'Resetting…' : 'Reset password'}
                </button>
              </form>
            </>
          ) : (
            <>
              <div role="alert" className={ALERT_SUCCESS}>
                <p className="font-medium mb-1">Password reset</p>
                <p>Your password has been reset. You can now sign in.</p>
              </div>

              <div className="mt-6">
                <Link
                  to="/login"
                  className="block w-full text-center px-4 py-2 text-sm font-medium text-content-inverse bg-accent hover:bg-accent-hover rounded transition-colors focus:outline-none focus:ring-2 focus:ring-border-focus"
                >
                  Go to login
                </Link>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
