import { usePermissions } from '../hooks/usePermission'

interface PermissionGateProps {
  /** Single permission key — user must hold this key. */
  permission?: string
  /** OR composite — user must hold at least one of these keys. */
  any?: string[]
  /** AND composite — user must hold all of these keys. */
  all?: string[]
  /** Rendered when the permission check fails. Defaults to null (render nothing). */
  fallback?: React.ReactNode
  children: React.ReactNode
}

/**
 * Renders `children` only when the current user holds the required permission(s).
 * Falls back to `fallback` (default: nothing) when the check fails.
 *
 * Usage:
 *   <PermissionGate permission="connections.manage">
 *     <Button>Create Connection</Button>
 *   </PermissionGate>
 *
 *   <PermissionGate any={["runs.execute", "runs.abort"]} fallback={<p>No access</p>}>
 *     ...
 *   </PermissionGate>
 */
export default function PermissionGate({
  permission,
  any: anyKeys,
  all: allKeys,
  fallback = null,
  children,
}: PermissionGateProps) {
  const permissions = usePermissions()

  let allowed = true

  if (permission !== undefined) {
    allowed = permissions.has(permission)
  }

  if (allowed && anyKeys !== undefined && anyKeys.length > 0) {
    allowed = anyKeys.some((k) => permissions.has(k))
  }

  if (allowed && allKeys !== undefined && allKeys.length > 0) {
    allowed = allKeys.every((k) => permissions.has(k))
  }

  return <>{allowed ? children : fallback}</>
}
