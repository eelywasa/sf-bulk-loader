/**
 * Backup-codes modal — shown once after TOTP enrolment or after a
 * backup-code regenerate (SFBL-250 / spec §2.1, §2.8). The user MUST
 * acknowledge the "I've saved these codes" checkbox before the modal
 * can be closed: once dismissed, the plaintext codes are unrecoverable.
 *
 * Props kept minimal + reusable so regenerate and enrolment can share the
 * same component.
 */

import { useState } from 'react'
import { Modal } from '../components/ui/Modal'
import { Button } from '../components/ui/Button'
import { ALERT_INFO, CHECKBOX_CLASS } from '../components/ui/formStyles'

export interface MfaBackupCodesModalProps {
  codes: string[]
  onClose: () => void
}

export default function MfaBackupCodesModal({ codes, onClose }: MfaBackupCodesModalProps) {
  const [acknowledged, setAcknowledged] = useState(false)
  const [copied, setCopied] = useState(false)

  const joined = codes.join('\n')

  function handleDownload() {
    const blob = new Blob([joined + '\n'], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'sfbl-backup-codes.txt'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(joined)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      // Silent — most browsers will have clipboard access. The user can fall
      // back to Download .txt if copy is unavailable.
    }
  }

  return (
    <Modal
      open
      onClose={() => {
        // Only allow closing once the user has ticked the checkbox.
        if (acknowledged) onClose()
      }}
      closeOnBackdropClick={false}
      title="Save your backup codes"
      description="Each code can be used once if you lose access to your authenticator app. Store them in a password manager or print and secure them."
      size="md"
      footer={
        <>
          <Button variant="secondary" onClick={handleCopy} data-testid="backup-codes-copy">
            {copied ? 'Copied' : 'Copy all'}
          </Button>
          <Button variant="secondary" onClick={handleDownload} data-testid="backup-codes-download">
            Download .txt
          </Button>
          <Button
            variant="primary"
            onClick={onClose}
            disabled={!acknowledged}
            data-testid="backup-codes-close"
          >
            Close
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className={ALERT_INFO}>
          These codes are shown once and will not be recoverable after you close this window.
        </div>

        <div
          className="bg-surface-sunken border border-border-base rounded-md p-4 max-h-64 overflow-y-auto"
          data-testid="backup-codes-list"
        >
          <ul className="font-mono text-sm space-y-1 text-content-primary">
            {codes.map((code) => (
              <li key={code}>{code}</li>
            ))}
          </ul>
        </div>

        <label className="flex items-start gap-2 text-sm text-content-primary">
          <input
            type="checkbox"
            className={CHECKBOX_CLASS + ' mt-0.5'}
            checked={acknowledged}
            onChange={(e) => setAcknowledged(e.target.checked)}
            data-testid="backup-codes-ack"
          />
          <span>I&rsquo;ve saved these codes somewhere safe.</span>
        </label>
      </div>
    </Modal>
  )
}
