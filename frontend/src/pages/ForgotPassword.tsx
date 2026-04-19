import { useState } from 'react'
import { Link } from 'react-router-dom'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import { faHexagonNodes } from '@fortawesome/free-solid-svg-icons'
import { ApiError } from '../api/client'
import { requestPasswordReset } from '../api/endpoints'
import { LABEL_CLASS, INPUT_CLASS, ALERT_ERROR, ALERT_WARNING, ALERT_SUCCESS } from '../components/ui/formStyles'

type PageState = 'form' | 'confirmation'

export default function ForgotPassword() {
  const [pageState, setPageState] = useState<PageState>('form')
  const [email, setEmail] = useState('')
  const [submittedEmail, setSubmittedEmail] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [warning, setWarning] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setWarning(null)
    setLoading(true)
    try {
      await requestPasswordReset({ email })
      setSubmittedEmail(email)
      setPageState('confirmation')
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 429) {
        setWarning('Too many requests — please try again later.')
      } else {
        setError('Something went wrong. Please try again.')
      }
    } finally {
      setLoading(false)
    }
  }

  function handleTryDifferentEmail() {
    setPageState('form')
    setError(null)
    setWarning(null)
  }

  return (
    <div className="min-h-dvh grid place-items-center bg-surface-base">
      <div className="w-full max-w-md px-4">
        <div className="bg-surface-raised rounded-lg shadow-sm border border-border-base p-8">
          <div className="flex items-center gap-2 mb-6">
            <div className="w-7 h-7 rounded bg-blue-600 flex items-center justify-center flex-shrink-0">
              <FontAwesomeIcon icon={faHexagonNodes} className="w-4 h-4 text-white" />
            </div>
            <span className="text-base font-semibold text-content-primary">
              Bulk Loader
            </span>
          </div>

          {pageState === 'form' ? (
            <>
              <h1 className="text-sm font-medium text-content-secondary mb-5">
                Reset your password
              </h1>

              <form onSubmit={handleSubmit} noValidate>
                {error && (
                  <div role="alert" className={`mb-4 ${ALERT_ERROR}`}>
                    {error}
                  </div>
                )}
                {warning && (
                  <div role="alert" className={`mb-4 ${ALERT_WARNING}`}>
                    {warning}
                  </div>
                )}

                <div className="mb-5">
                  <label htmlFor="email" className={LABEL_CLASS}>
                    Email address
                  </label>
                  <input
                    id="email"
                    type="email"
                    autoComplete="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    className={INPUT_CLASS}
                  />
                </div>

                <button
                  type="submit"
                  disabled={loading}
                  className="w-full px-4 py-2 text-sm font-medium text-content-inverse bg-accent hover:bg-accent-hover disabled:opacity-50 rounded transition-colors focus:outline-none focus:ring-2 focus:ring-border-focus"
                >
                  {loading ? 'Sending…' : 'Send reset link'}
                </button>
              </form>

              <div className="mt-4 text-center">
                <Link
                  to="/login"
                  className="text-xs text-accent hover:text-accent-hover"
                >
                  Back to login
                </Link>
              </div>
            </>
          ) : (
            <>
              <div role="alert" className={ALERT_SUCCESS}>
                <p className="font-medium mb-1">Check your inbox</p>
                <p>
                  If an account exists for <strong>{submittedEmail}</strong>, a reset link has
                  been sent. Please check your inbox and spam folder. The link expires in 15
                  minutes.
                </p>
              </div>

              <div className="mt-6 space-y-3">
                <Link
                  to="/login"
                  className="block w-full text-center px-4 py-2 text-sm font-medium text-content-inverse bg-accent hover:bg-accent-hover rounded transition-colors focus:outline-none focus:ring-2 focus:ring-border-focus"
                >
                  Back to login
                </Link>
                <button
                  type="button"
                  onClick={handleTryDifferentEmail}
                  className="block w-full text-center text-xs text-accent hover:text-accent-hover"
                >
                  Try a different email
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
