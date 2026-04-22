import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

interface ProtectedRouteProps {
  children: React.ReactNode
  /**
   * Optional permission key. If set and the current user does not hold the key,
   * they are redirected to /403 with the required key in location state.
   */
  permission?: string
}

export default function ProtectedRoute({ children, permission }: ProtectedRouteProps) {
  const { token, permissions, isBootstrapping, authRequired } = useAuth()
  const location = useLocation()

  if (isBootstrapping) {
    return (
      <div className="flex items-center justify-center h-screen bg-surface-base">
        <p className="text-sm text-content-disabled" aria-label="Loading" />
      </div>
    )
  }

  if (authRequired && !token) {
    const next = encodeURIComponent(location.pathname + location.search)
    return <Navigate to={`/login?next=${next}`} replace />
  }

  // Permission gate — applies when user is authenticated (or desktop, which always has perms)
  if (permission && !permissions.has(permission)) {
    return <Navigate to="/403" state={{ requiredPermission: permission }} replace />
  }

  return <>{children}</>
}
