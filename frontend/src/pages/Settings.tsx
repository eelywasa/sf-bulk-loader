/**
 * Settings page.
 *
 * Desktop profile: shows the Storage tab (configurable input/output dirs).
 * Hosted profiles: admin settings live on dedicated /settings/<area> pages
 * (SFBL-157, SFBL-353), so a bare /settings just redirects to the first one.
 * This page is kept only as the desktop Storage entry point + a backward-compat
 * redirect for stale hosted bookmarks.
 */

import { Navigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { StorageTab } from './settings/StorageTab'

export default function Settings() {
  const { authRequired } = useAuth()

  // Hosted profiles: dedicated pages own each settings area — send /settings to one.
  if (authRequired) {
    return <Navigate to="/settings/email" replace />
  }

  // Desktop profile (auth_mode=none): show storage settings only.
  return (
    <div className="p-6 max-w-4xl">
      <h1 className="text-xl font-semibold text-content-primary mb-6">Settings</h1>
      <StorageTab />
    </div>
  )
}
