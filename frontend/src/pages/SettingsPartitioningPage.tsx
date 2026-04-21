/**
 * SettingsPartitioningPage — DB-backed partitioning / concurrency settings (SFBL-157).
 */

import { SettingsPageShell } from './SettingsPageShell'

export default function SettingsPartitioningPage() {
  return (
    <SettingsPageShell
      category="partitioning"
      title="Partitioning Settings"
    />
  )
}
