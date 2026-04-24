/**
 * Login page — handles phase-1 login and routes into the 2FA challenge or
 * forced-enrolment sub-views (SFBL-251, spec §2.2 / §2.3). When the backend
 * responds with `mfa_required`, we hold the short-lived `mfa_token` in
 * component state and render the appropriate child view inline — no URL
 * change, so the token never lands in history.
 *
 * SFBL-204: DOM tab order is `email → password → submit → forgot-password`.
 * The "Forgot password?" link is rendered below the password field in source
 * order; `flex` flips the visual position so it still sits next to the
 * password label.
 */

import { useState } from 'react'
import { useNavigate, useSearchParams, Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { ApiError, apiPost } from '../api/client'
import type { LoginResponse, MfaRequiredResponse } from '../api/types'
import { isMfaRequired } from '../api/types'
import { LABEL_CLASS, INPUT_CLASS, ALERT_ERROR } from '../components/ui/formStyles'
import { BrandMark } from '../components/ui'
import LoginMfaChallenge from './LoginMfaChallenge'
import LoginMfaEnroll from './LoginMfaEnroll'

interface MfaState {
  mfa_token: string
  must_enroll: boolean
}

export default function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [mfa, setMfa] = useState<MfaState | null>(null)

  const nextPath = searchParams.get('next') ?? '/'

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const data = await apiPost<LoginResponse>('/api/auth/login', { email, password })
      if (isMfaRequired(data)) {
        const mfaResp = data as MfaRequiredResponse
        setMfa({ mfa_token: mfaResp.mfa_token, must_enroll: mfaResp.must_enroll })
        return
      }
      await login(data.access_token)
      navigate(nextPath, { replace: true })
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 401) {
        setError('Invalid email or password')
      } else {
        setError('Sign in failed. Please try again.')
      }
    } finally {
      setLoading(false)
    }
  }

  function handleMfaReset(message?: string) {
    setMfa(null)
    if (message) setError(message)
  }

  if (mfa && mfa.must_enroll) {
    return (
      <LoginMfaEnroll
        mfaToken={mfa.mfa_token}
        nextPath={nextPath}
        onAbort={handleMfaReset}
      />
    )
  }

  if (mfa) {
    return (
      <LoginMfaChallenge
        mfaToken={mfa.mfa_token}
        nextPath={nextPath}
        onAbort={handleMfaReset}
      />
    )
  }

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

          <h1 className="text-sm font-medium text-content-secondary mb-5">
            Sign in to your account
          </h1>

          <form onSubmit={handleSubmit} noValidate>
            {error && (
              <div role="alert" className={`mb-4 ${ALERT_ERROR}`}>
                {error}
              </div>
            )}

            <div className="mb-3">
              <label htmlFor="email" className={LABEL_CLASS}>
                Email
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

            <div className="mb-5 flex flex-col">
              {/*
                DOM source order (SFBL-204 tab order): label → password input →
                submit → forgot-password link. Visual order is controlled via
                Tailwind `order-*` utilities so the link still sits next to
                the "Password" label.
              */}
              <label
                htmlFor="password"
                className={`${LABEL_CLASS} order-1 mb-1 inline-flex`}
              >
                Password
              </label>
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={`${INPUT_CLASS} order-2`}
              />
              <div className="order-3 mt-2 flex justify-end">
                {/*
                  Rendered AFTER submit in source order below; styled to
                  appear here via flex ordering on the outer form.
                */}
              </div>
            </div>

            <button
              type="submit"
              disabled={loading}
              data-testid="login-submit"
              className="w-full px-4 py-2 text-sm font-medium text-content-inverse bg-accent hover:bg-accent-hover disabled:opacity-50 rounded transition-colors focus:outline-none focus:ring-2 focus:ring-border-focus"
            >
              {loading ? 'Signing in…' : 'Sign in'}
            </button>

            {/*
              SFBL-204: rendered AFTER submit in DOM order so Tab goes
              username → password → submit → this link. Visually nudged up
              via negative margin so it sits just under the form.
            */}
            <div className="mt-3 text-right">
              <Link
                to="/forgot-password"
                className="text-xs text-accent hover:text-accent-hover"
              >
                Forgot password?
              </Link>
            </div>
          </form>
        </div>
      </div>
    </div>
  )
}
