import { createContext, useContext, useEffect, useState } from 'react'
import { apiFetch, clearStoredToken, getStoredToken, storeToken } from '../api/client'
import type { RuntimeConfig, UserResponse } from '../api/types'

interface AuthContextValue {
  token: string | null
  user: UserResponse | null
  isBootstrapping: boolean
  /** False when the active distribution profile requires no login (e.g. desktop). */
  authRequired: boolean
  login: (token: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(null)
  const [user, setUser] = useState<UserResponse | null>(null)
  const [isBootstrapping, setIsBootstrapping] = useState(true)
  const [authRequired, setAuthRequired] = useState(true)

  useEffect(() => {
    apiFetch<RuntimeConfig>('/api/runtime')
      .then((cfg) => {
        if (cfg.auth_mode === 'none') {
          // Desktop / no-login profile — skip token validation entirely
          setAuthRequired(false)
          setIsBootstrapping(false)
          return
        }

        // Hosted profile — validate any stored token
        setAuthRequired(true)
        const stored = getStoredToken()
        if (!stored) {
          setIsBootstrapping(false)
          return
        }
        setToken(stored)
        apiFetch<UserResponse>('/api/auth/me')
          .then((u) => setUser(u))
          .catch(() => {
            // Token was invalid — client already cleared it; reset local state
            setToken(null)
          })
          .finally(() => setIsBootstrapping(false))
      })
      .catch(() => {
        // Could not reach /api/runtime — fall back to requiring auth
        const stored = getStoredToken()
        if (!stored) {
          setIsBootstrapping(false)
          return
        }
        setToken(stored)
        apiFetch<UserResponse>('/api/auth/me')
          .then((u) => setUser(u))
          .catch(() => setToken(null))
          .finally(() => setIsBootstrapping(false))
      })
  }, [])

  async function login(newToken: string): Promise<void> {
    storeToken(newToken)
    setToken(newToken)
    const u = await apiFetch<UserResponse>('/api/auth/me')
    setUser(u)
  }

  function logout(): void {
    clearStoredToken()
    setToken(null)
    setUser(null)
    if (authRequired) {
      window.location.href = '/login'
    }
  }

  return (
    <AuthContext.Provider value={{ token, user, isBootstrapping, authRequired, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}

/**
 * Non-throwing variant — returns null when no AuthProvider is mounted.
 * Useful for components that appear on pages whose existing tests do not
 * wrap in AuthProvider (e.g. the PlanEditor toolbar).
 */
export function useAuthOptional(): AuthContextValue | null {
  return useContext(AuthContext)
}
