/**
 * SettingsSecurityPage — DB-backed login lockout / rate-limit + 2FA
 * enforcement settings (SFBL-157 / SFBL-251).
 *
 * The shell auto-renders every setting in the `security` category as a form
 * field; we layer on category-specific callouts for settings whose change
 * has non-obvious consequences:
 *
 * - Rate-limit window changes apply to new request windows only.
 * - `require_2fa` does not sign out existing sessions; users without a factor
 *   are forced to enrol on next login (spec §2.7, §12.2).
 */

import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import {
  faTriangleExclamation,
  faShield,
} from '@fortawesome/free-solid-svg-icons'
import { SettingsPageShell } from './SettingsPageShell'
import { ALERT_INFO, ALERT_WARNING } from '../components/ui/formStyles'

const RATE_LIMIT_CALLOUT = (
  <div className={`${ALERT_WARNING} mb-4 flex items-start gap-2`}>
    <FontAwesomeIcon
      icon={faTriangleExclamation}
      className="w-4 h-4 flex-shrink-0 mt-0.5"
      aria-hidden="true"
    />
    <p>
      Changes to rate-limit windows apply to <strong>new</strong> request
      windows only; in-flight windows use the previously-configured value.
    </p>
  </div>
)

const REQUIRE_2FA_CALLOUT = (
  <div
    className={`${ALERT_INFO} mb-6 flex items-start gap-2`}
    data-testid="require-2fa-warning"
  >
    <FontAwesomeIcon
      icon={faShield}
      className="w-4 h-4 flex-shrink-0 mt-0.5"
      aria-hidden="true"
    />
    <p>
      <strong>Turning on <code>require_2fa</code>:</strong> existing sessions
      remain signed in; users without a factor will be required to enrol on
      their next login.
    </p>
  </div>
)

const PREAMBLE = (
  <>
    {RATE_LIMIT_CALLOUT}
    {REQUIRE_2FA_CALLOUT}
  </>
)

export default function SettingsSecurityPage() {
  return (
    <SettingsPageShell
      category="security"
      title="Security Settings"
      preamble={PREAMBLE}
    />
  )
}
