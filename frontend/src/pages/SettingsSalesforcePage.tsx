/**
 * SettingsSalesforcePage — DB-backed Salesforce connection settings (SFBL-157).
 *
 * Note: polling intervals use exponential backoff (floor → ceiling).
 * Helper text on the relevant fields hints at this relationship.
 */

import { SettingsPageShell } from './SettingsPageShell'

export default function SettingsSalesforcePage() {
  return (
    <SettingsPageShell
      category="salesforce"
      title="Salesforce Settings"
    />
  )
}
