/**
 * SettingsNotificationsPage — per-user notification subscriptions (SFBL-353).
 *
 * Promotes the NotificationsTab content to a standalone /settings/notifications
 * page, matching the SFBL-157 admin-settings layout so it is reachable from the
 * sidebar Settings menu on hosted profiles. Unlike the email/salesforce pages
 * this is a CRUD list rather than a DB-backed key/value category, so it does
 * not use SettingsPageShell.
 */

import { NotificationsTab } from './settings/NotificationsTab'

export default function SettingsNotificationsPage() {
  return (
    <div className="p-6 max-w-4xl">
      <h1 className="text-xl font-semibold text-content-primary mb-6">
        Notification Settings
      </h1>
      <NotificationsTab />
    </div>
  )
}
