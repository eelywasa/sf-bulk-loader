import { createContext, useContext, useEffect, useState } from 'react'
import { apiFetch, clearStoredToken, getStoredToken, storeToken } from '../api/client'
import type { RuntimeConfig, UserResponse } from '../api/types'

// All permission keys from the RBAC spec §5.1
export const ALL_PERMISSION_KEYS = [
  'connections.view',
  'connections.view_credentials',
  'connections.manage',
  'plans.view',
  'plans.manage',
  'runs.view',
  'runs.execute',
  'runs.abort',
  'files.view',
  'files.view_contents',
  'users.manage',
  'system.settings',
] as const

export type PermissionKey = typeof ALL_PERMISSION_KEYS[number]

function permissionsFromUser(u: UserResponse | null): Set<string> {
  if (!u) return new Set()
  if (u.permissions && Array.isArray(u.permissions)) {
    return new Set(u.permissions)
  }
  // Fallback: if no permissions array (pre-SFBL-195 backend), derive from role
  if (u.role === 'admin' || u.is_admin) {
    return new Set(ALL_PERMISSION_KEYS)
  }
  return new Set()
}

function profileNameFromUser(u: UserResponse | null): string | null {
  if (!u) return null
  if (u.profile?.name) return u.profile.name
  // Fallback for old backend
  return u.role ?? null
}

interface AuthContextValue {
  token: string | null
  user: UserResponse | null
  permissions: Set<string>
  profileName: string | null
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
  const [permissions, setPermissions] = useState<Set<string>>(new Set())
  const [profileName, setProfileName] = useState<string | null>(null)
  const [isBootstrapping, setIsBootstrapping] = useState(true)
  const [authRequired, setAuthRequired] = useState(true)

  function applyUser(u: UserResponse | null) {
    setUser(u)
    setPermissions(permissionsFromUser(u))
    setProfileName(profileNameFromUser(u))
  }

  useEffect(() => {
    apiFetch<RuntimeConfig>('/api/runtime')
      .then((cfg) => {
        if (cfg.auth_mode === 'none') {
          // Desktop / no-login profile — fetch /me to get permissions for the virtual desktop user
          setAuthRequired(false)
          apiFetch<UserResponse>('/api/auth/me')
            .then((u) => applyUser(u))
            .catch(() => {
              // /me not yet returning permissions (pre-SFBL-195 backend); use full set for desktop
              applyUser({
                id: 'desktop',
                username: 'desktop',
                email: null,
                display_name: null,
                permissions: [...ALL_PERMISSION_KEYS],
                profile: { name: 'desktop' },
              })
            })
            .finally(() => setIsBootstrapping(false))
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
          .then((u) => applyUser(u))
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
          .then((u) => applyUser(u))
          .catch(() => setToken(null))
          .finally(() => setIsBootstrapping(false))
      })
  }, [])

  async function login(newToken: string): Promise<void> {
    storeToken(newToken)
    setToken(newToken)
    const u = await apiFetch<UserResponse>('/api/auth/me')
    applyUser(u)
  }

  function logout(): void {
    clearStoredToken()
    setToken(null)
    applyUser(null)
    if (authRequired) {
      window.location.href = '/login'
    }
  }

  return (
    <AuthContext.Provider
      value={{ token, user, permissions, profileName, isBootstrapping, authRequired, login, logout }}
    >
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
