import { createContext, useContext, useEffect, useState } from 'react'
import { apiFetch, clearStoredToken, getStoredToken, storeToken } from '../api/client'
import type { UserResponse } from '../api/types'

interface AuthContextValue {
  token: string | null
  user: UserResponse | null
  isBootstrapping: boolean
  login: (token: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(null)
  const [user, setUser] = useState<UserResponse | null>(null)
  const [isBootstrapping, setIsBootstrapping] = useState(true)

  useEffect(() => {
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
    window.location.href = '/login'
  }

  return (
    <AuthContext.Provider value={{ token, user, isBootstrapping, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
