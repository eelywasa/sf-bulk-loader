import { useAuthOptional } from '../context/AuthContext'

/**
 * Returns true if the current user holds the given permission key.
 *
 * In desktop mode (auth_mode=none) the virtual desktop user is seeded with all
 * permission keys, so this always returns true — no special-casing needed here.
 *
 * When called outside an AuthProvider (e.g. in tests that don't set one up),
 * returns false rather than throwing. This preserves backward-compatibility
 * with existing page tests.
 */
export function usePermission(key: string): boolean {
  const auth = useAuthOptional()
  if (!auth) return false
  return auth.permissions.has(key)
}

/**
 * Returns the full permissions Set for callers that need to check multiple keys.
 * Returns an empty Set when called outside an AuthProvider.
 */
export function usePermissions(): Set<string> {
  const auth = useAuthOptional()
  if (!auth) return new Set()
  return auth.permissions
}
