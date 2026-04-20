/**
 * Settings page — tabbed shell (Email / Notifications).
 *
 * Desktop profile (auth_mode=none) has no tabs: there is no user identity
 * for Notifications, and admin Email is hosted-only. We show a short hint
 * instead.
 */

import { useAuth } from '../context/AuthContext'
import { Tabs } from '../components/ui/Tabs'
import type { TabItem } from '../components/ui/Tabs'
import { EmailTab } from './settings/EmailTab'
import { NotificationsTab } from './settings/NotificationsTab'

export default function Settings() {
  const { authRequired } = useAuth()

  if (!authRequired) {
    return (
      <div className="p-6 max-w-2xl">
        <h1 className="text-xl font-semibold text-content-primary mb-6">Settings</h1>
        <p className="text-sm text-content-muted">
          No configurable settings are available in desktop mode.
        </p>
      </div>
    )
  }

  const tabs: TabItem[] = [
    { id: 'email', label: 'Email', content: <EmailTab /> },
    { id: 'notifications', label: 'Notifications', content: <NotificationsTab /> },
  ]

  return (
    <div className="p-6 max-w-4xl">
      <h1 className="text-xl font-semibold text-content-primary mb-6">Settings</h1>
      <Tabs tabs={tabs} />
    </div>
  )
}
