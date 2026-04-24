/**
 * Self-service TOTP enrolment wizard (SFBL-250 / spec §2.1).
 *
 * Three steps, rendered inside a modal:
 *   1. Intro — what this is + what the user needs.
 *   2. QR + secret — calls `/2fa/enroll/start` once and displays the SVG
 *      returned by the backend plus the base32 secret for manual entry.
 *      The secret lives in component state ONLY — never localStorage /
 *      sessionStorage per D11 / spec §10.7.
 *   3. Confirm — 6-digit code entry; on success swaps the auth token and
 *      hands off to `MfaBackupCodesModal` to reveal the one-shot set.
 */

import { useEffect, useState } from 'react'
import { Modal } from '../components/ui/Modal'
import { Button } from '../components/ui/Button'
import { Spinner } from '../components/ui/Spinner'
import {
  ALERT_ERROR,
  ALERT_INFO,
  INPUT_CLASS,
  LABEL_CLASS,
} from '../components/ui/formStyles'
import { mfaApi } from '../api/endpoints'
import { ApiError } from '../api/client'
import { useAuth } from '../context/AuthContext'
import MfaBackupCodesModal from './MfaBackupCodesModal'
import type { MfaEnrollStartResponse } from '../api/types'

type Step = 'intro' | 'scan' | 'confirm' | 'codes'

export interface MfaEnrollWizardProps {
  open: boolean
  onClose: () => void
  /** Fires after the wizard fully completes (codes acknowledged). */
  onEnrolled?: () => void
}

function extractMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return 'Something went wrong. Please try again.'
}

export default function MfaEnrollWizard({ open, onClose, onEnrolled }: MfaEnrollWizardProps) {
  const { login } = useAuth()
  const [step, setStep] = useState<Step>('intro')
  const [startData, setStartData] = useState<MfaEnrollStartResponse | null>(null)
  const [startError, setStartError] = useState<string | null>(null)
  const [loadingStart, setLoadingStart] = useState(false)
  const [code, setCode] = useState('')
  const [confirmError, setConfirmError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [codes, setCodes] = useState<string[] | null>(null)

  // Reset wizard state whenever it re-opens — the secret must NEVER be
  // retained across open/close cycles.
  useEffect(() => {
    if (open) {
      setStep('intro')
      setStartData(null)
      setStartError(null)
      setCode('')
      setConfirmError(null)
      setCodes(null)
    }
  }, [open])

  async function advanceToScan() {
    setLoadingStart(true)
    setStartError(null)
    try {
      const data = await mfaApi.enrollStart()
      setStartData(data)
      setStep('scan')
    } catch (err) {
      setStartError(extractMessage(err))
    } finally {
      setLoadingStart(false)
    }
  }

  async function handleConfirm(e: React.FormEvent) {
    e.preventDefault()
    if (!startData) return
    setSubmitting(true)
    setConfirmError(null)
    try {
      const res = await mfaApi.enrollConfirm({
        secret_base32: startData.secret_base32,
        code: code.trim(),
      })
      // Swap the stored token — the confirm response invalidates the prior
      // one via the password_changed_at watermark.
      await login(res.access_token)
      setCodes(res.backup_codes)
      // Drop the plaintext secret from memory as soon as confirm succeeds.
      setStartData(null)
      setStep('codes')
    } catch (err) {
      setConfirmError(extractMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  function handleCodesClosed() {
    setCodes(null)
    onEnrolled?.()
    onClose()
  }

  // ─── Step: codes (delegates to backup codes modal) ──────────────────────
  if (open && step === 'codes' && codes) {
    return <MfaBackupCodesModal codes={codes} onClose={handleCodesClosed} />
  }

  // ─── Intro ──────────────────────────────────────────────────────────────
  if (step === 'intro') {
    return (
      <Modal
        open={open}
        onClose={onClose}
        title="Set up two-factor authentication"
        description="You'll need an authenticator app such as 1Password, Authy, or Google Authenticator."
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={onClose}>Cancel</Button>
            <Button
              variant="primary"
              onClick={advanceToScan}
              loading={loadingStart}
              disabled={loadingStart}
            >
              Continue
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm text-content-secondary">
          <p>
            Two-factor authentication adds a second step to your sign-in: a
            6-digit code from an app on your phone. We'll also generate one-time
            backup codes you can use if you lose access to the app.
          </p>
          {startError && (
            <div className={ALERT_ERROR} role="alert">{startError}</div>
          )}
        </div>
      </Modal>
    )
  }

  // ─── Scan QR ────────────────────────────────────────────────────────────
  if (step === 'scan') {
    return (
      <Modal
        open={open}
        onClose={onClose}
        title="Scan the QR code"
        description="Open your authenticator app and scan the code, or paste the secret for manual entry."
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={onClose}>Cancel</Button>
            <Button
              variant="primary"
              onClick={() => setStep('confirm')}
              disabled={!startData}
            >
              Next
            </Button>
          </>
        }
      >
        {!startData ? (
          <div className="flex justify-center py-8"><Spinner /></div>
        ) : (
          <div className="space-y-4">
            <div
              className="flex justify-center bg-white p-4 rounded-md border border-border-base"
              aria-label="TOTP QR code"
              data-testid="mfa-qr"
              dangerouslySetInnerHTML={{ __html: startData.qr_svg }}
            />
            <div>
              <div className={LABEL_CLASS}>Or enter this secret manually</div>
              <div
                className="font-mono text-sm break-all bg-surface-sunken border border-border-base rounded-md p-3 text-content-primary"
                data-testid="mfa-secret"
              >
                {startData.secret_base32}
              </div>
            </div>
          </div>
        )}
      </Modal>
    )
  }

  // ─── Confirm code ───────────────────────────────────────────────────────
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Enter the 6-digit code"
      description="Enter the code shown in your authenticator app to finish setup."
      size="md"
      footer={
        <>
          <Button variant="secondary" onClick={() => setStep('scan')}>Back</Button>
          <Button
            type="submit"
            form="mfa-confirm-form"
            variant="primary"
            loading={submitting}
            disabled={submitting || code.trim().length < 6}
          >
            Verify and enable
          </Button>
        </>
      }
    >
      <form id="mfa-confirm-form" onSubmit={handleConfirm} className="space-y-4">
        <div>
          <label className={LABEL_CLASS} htmlFor="mfa-code">Authenticator code</label>
          <input
            id="mfa-code"
            type="text"
            inputMode="numeric"
            pattern="[0-9]*"
            autoComplete="one-time-code"
            maxLength={6}
            value={code}
            onChange={(e) => { setCode(e.target.value.replace(/\D/g, '')); setConfirmError(null) }}
            className={INPUT_CLASS + ' font-mono tracking-widest text-center'}
            placeholder="123456"
            autoFocus
          />
        </div>
        <div className={ALERT_INFO}>
          After you verify the code, you'll receive 10 one-time backup codes.
          Save them in a secure place.
        </div>
        {confirmError && (
          <div className={ALERT_ERROR} role="alert">{confirmError}</div>
        )}
      </form>
    </Modal>
  )
}
