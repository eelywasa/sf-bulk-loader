/**
 * SettingsSecurityPage — DB-backed login lockout / rate-limit settings (SFBL-157).
 *
 * Includes a callout explaining that rate-limit window changes apply to new
 * request windows only; in-flight windows use the previously-configured value.
 */

import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import { faTriangleExclamation } from '@fortawesome/free-solid-svg-icons'
import { SettingsPageShell } from './SettingsPageShell'
import { ALERT_WARNING } from '../components/ui/formStyles'

const RATE_LIMIT_CALLOUT = (
  <div className={`${ALERT_WARNING} mb-6 flex items-start gap-2`}>
    <FontAwesomeIcon icon={faTriangleExclamation} className="w-4 h-4 flex-shrink-0 mt-0.5" />
    <p>
      Changes to rate-limit windows apply to <strong>new</strong> request windows only; in-flight
      windows use the previously-configured value.
    </p>
  </div>
)

export default function SettingsSecurityPage() {
  return (
    <SettingsPageShell
      category="security"
      title="Security Settings"
      preamble={RATE_LIMIT_CALLOUT}
    />
  )
}
