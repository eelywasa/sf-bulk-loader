import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

interface ProtectedRouteProps {
  children: React.ReactNode
}

export default function ProtectedRoute({ children }: ProtectedRouteProps) {
  const { token, isBootstrapping, authRequired } = useAuth()
  const location = useLocation()

  if (isBootstrapping) {
    return (
      <div className="flex items-center justify-center h-screen bg-surface-base">
        <p className="text-sm text-content-disabled" aria-label="Loading" />
      </div>
    )
  }

  if (!authRequired) {
    return <>{children}</>
  }

  if (!token) {
    const next = encodeURIComponent(location.pathname + location.search)
    return <Navigate to={`/login?next=${next}`} replace />
  }

  return <>{children}</>
}
