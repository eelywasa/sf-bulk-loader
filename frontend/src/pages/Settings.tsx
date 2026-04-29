/**
 * Settings page.
 *
 * Desktop profile: shows the Storage tab (configurable input/output dirs).
 * Hosted profiles: shows Email and Notifications tabs (profile-gated admin settings).
 */

import { useAuth } from '../context/AuthContext'
import { Tabs } from '../components/ui/Tabs'
import type { TabItem } from '../components/ui/Tabs'
import { EmailTab } from './settings/EmailTab'
import { NotificationsTab } from './settings/NotificationsTab'
import { StorageTab } from './settings/StorageTab'

export default function Settings() {
  const { authRequired } = useAuth()

  // Desktop profile (auth_mode=none): show storage settings only.
  if (!authRequired) {
    return (
      <div className="p-6 max-w-4xl">
        <h1 className="text-xl font-semibold text-content-primary mb-6">Settings</h1>
        <StorageTab />
      </div>
    )
  }

  // Hosted profiles: show admin-only email and notification settings.
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
