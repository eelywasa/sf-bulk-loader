import { Link, useLocation } from 'react-router-dom'

interface ForbiddenLocationState {
  requiredPermission?: string
}

export default function ForbiddenPage() {
  const location = useLocation()
  const state = location.state as ForbiddenLocationState | null
  const requiredPermission = state?.requiredPermission

  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] p-6 text-center">
      <div className="max-w-md space-y-4">
        <div className="text-6xl font-bold text-content-muted">403</div>
        <h1 className="text-2xl font-semibold text-content-primary">Access denied</h1>
        <p className="text-content-secondary">
          You don&apos;t have permission to view this page.
          {requiredPermission && (
            <>
              {' '}
              Required permission:{' '}
              <code className="font-mono text-sm bg-surface-raised px-1.5 py-0.5 rounded border border-border-base">
                {requiredPermission}
              </code>
            </>
          )}
        </p>
        <p className="text-sm text-content-muted">
          Contact your administrator if you believe this is a mistake.
        </p>
        <Link
          to="/"
          className="inline-block mt-2 px-4 py-2 rounded bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 transition-colors"
        >
          Back to dashboard
        </Link>
      </div>
    </div>
  )
}
