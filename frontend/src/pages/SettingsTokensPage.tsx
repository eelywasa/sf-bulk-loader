/**
 * SettingsTokensPage — Personal Access Token management (SFBL-369).
 *
 * Route: /settings/tokens
 * Permission gate: tokens.manage (via PermissionGate — users without the
 * permission see a 403-style message rather than a redirect, since the profile
 * page itself is accessible to all authenticated users).
 *
 * Unlike the DB-backed admin settings pages this is a CRUD list, so it does
 * not use SettingsPageShell.
 */

import PermissionGate from '../components/PermissionGate'
import { PersonalAccessTokensTab } from './settings/PersonalAccessTokensTab'

export default function SettingsTokensPage() {
  return (
    <div className="p-6 max-w-4xl">
      <h1 className="text-xl font-semibold text-content-primary mb-1">
        Personal Access Tokens
      </h1>
      <p className="text-sm text-content-muted mb-6">
        Manage API tokens for programmatic access to this Bulk Loader instance.
      </p>
      <PermissionGate
        permission="tokens.manage"
        fallback={
          <div className="bg-surface-raised border border-border-base rounded-lg p-8 text-center">
            <p className="text-sm text-content-secondary">
              You do not have permission to manage Personal Access Tokens.
            </p>
          </div>
        }
      >
        <PersonalAccessTokensTab />
      </PermissionGate>
    </div>
  )
}
