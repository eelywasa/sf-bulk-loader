import { useState } from 'react'
import { useNavigate, useSearchParams, Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { ApiError, apiPost } from '../api/client'
import type { TokenResponse } from '../api/types'
import { LABEL_CLASS, INPUT_CLASS, ALERT_ERROR } from '../components/ui/formStyles'
import { BrandMark } from '../components/ui'

export default function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const data = await apiPost<TokenResponse>('/api/auth/login', { email, password })
      await login(data.access_token)
      const next = searchParams.get('next') ?? '/'
      navigate(next, { replace: true })
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

            <div className="mb-5">
              <div className="flex items-center justify-between mb-1">
                <label htmlFor="password" className={LABEL_CLASS}>
                  Password
                </label>
                <Link
                  to="/forgot-password"
                  className="text-xs text-accent hover:text-accent-hover"
                >
                  Forgot password?
                </Link>
              </div>
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={INPUT_CLASS}
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full px-4 py-2 text-sm font-medium text-content-inverse bg-accent hover:bg-accent-hover disabled:opacity-50 rounded transition-colors focus:outline-none focus:ring-2 focus:ring-border-focus"
            >
              {loading ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
