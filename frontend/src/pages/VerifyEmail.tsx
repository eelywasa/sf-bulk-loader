/**
 * Email change confirmation landing page.
 *
 * Route: /verify-email/:token (public — user arrives from a link in an email)
 * On mount, POSTs the token to /api/me/email-change/confirm.
 * SFBL-149
 */

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useParams } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { meApi } from '../api/endpoints'
import { ApiError } from '../api/client'
import { ALERT_ERROR, ALERT_SUCCESS } from '../components/ui/formStyles'

type VerifyState = 'loading' | 'success' | 'failure'

export default function VerifyEmail() {
  const { token } = useParams<{ token: string }>()
  const { token: authToken, authRequired } = useAuth()
  const isAuthed = !authRequired || Boolean(authToken)

  const [state, setState] = useState<VerifyState>('loading')
  const [errorMessage, setErrorMessage] = useState<string>('')

  useEffect(() => {
    if (!token) {
      setState('failure')
      setErrorMessage('Verification token is missing.')
      return
    }

    let cancelled = false

    meApi.confirmEmailChange({ token })
      .then(() => {
        if (!cancelled) setState('success')
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState('failure')
          if (err instanceof ApiError) {
            setErrorMessage(err.message)
          } else if (err instanceof Error) {
            setErrorMessage(err.message)
          } else {
            setErrorMessage('Email verification failed. The link may have expired or already been used.')
          }
        }
      })

    return () => { cancelled = true }
  }, [token])

  return (
    <div className="min-h-screen bg-surface-base flex items-center justify-center p-6">
      <div className="w-full max-w-md space-y-6">
        <h1 className="text-xl font-semibold text-content-primary text-center">
          Email verification
        </h1>

        {state === 'loading' && (
          <p className="text-sm text-content-muted text-center" aria-live="polite">
            Verifying your email address…
          </p>
        )}

        {state === 'success' && (
          <div className="space-y-4">
            <div className={ALERT_SUCCESS} role="status">
              Your email address has been updated successfully.
            </div>
            {isAuthed ? (
              <Link
                to="/profile"
                className="inline-flex items-center justify-center rounded-md font-medium px-4 py-2 text-sm bg-accent text-content-inverse hover:bg-accent-hover border border-transparent transition-colors duration-150"
              >
                Return to profile
              </Link>
            ) : (
              <Link
                to="/login"
                className="inline-flex items-center justify-center rounded-md font-medium px-4 py-2 text-sm bg-accent text-content-inverse hover:bg-accent-hover border border-transparent transition-colors duration-150"
              >
                Sign in
              </Link>
            )}
          </div>
        )}

        {state === 'failure' && (
          <div className="space-y-4">
            <div className={ALERT_ERROR} role="alert">
              {errorMessage || 'Email verification failed. The link may have expired or already been used.'}
            </div>
            {isAuthed ? (
              <p className="text-sm text-content-muted">
                <Link to="/profile" className="underline text-content-secondary hover:text-content-primary">
                  Return to profile
                </Link>
              </p>
            ) : (
              <p className="text-sm text-content-muted">
                <Link to="/login" className="underline text-content-secondary hover:text-content-primary">
                  Go to sign in
                </Link>
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
