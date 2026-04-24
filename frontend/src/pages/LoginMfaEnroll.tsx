/**
 * LoginMfaEnroll — forced-enrolment view for a user who logged in with a
 * valid password but has no 2FA factor configured AND the tenant has
 * `require_2fa` turned on (spec §2.3).
 *
 * Unlike the self-service `MfaEnrollWizard`, this flow uses the pre-auth
 * `/api/auth/login/2fa/enroll/*` endpoints with `mfa_token` as the bearer —
 * the user does not yet hold a full-access JWT.
 *
 * Three stateless steps (spec §0 D11 — nothing persisted until verify):
 *   1. Scan — mint + display QR / secret.
 *   2. Confirm — 6-digit code.
 *   3. Codes — one-shot backup-code reveal, reusing MfaBackupCodesModal.
 */

import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { ApiError } from '../api/client'
import { loginMfaApi } from '../api/endpoints'
import type { Login2faEnrollStartResponse } from '../api/types'
import {
  ALERT_ERROR,
  ALERT_INFO,
  INPUT_CLASS,
  LABEL_CLASS,
} from '../components/ui/formStyles'
import { BrandMark, Button, Spinner } from '../components/ui'
import MfaBackupCodesModal from './MfaBackupCodesModal'

export interface LoginMfaEnrollProps {
  mfaToken: string
  nextPath: string
  onAbort: (message?: string) => void
}

type Step = 'scan' | 'confirm' | 'codes'

function extractMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return 'Something went wrong. Please try again.'
}

export default function LoginMfaEnroll({
  mfaToken,
  nextPath,
  onAbort,
}: LoginMfaEnrollProps) {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [step, setStep] = useState<Step>('scan')
  const [startData, setStartData] = useState<Login2faEnrollStartResponse | null>(null)
  const [startError, setStartError] = useState<string | null>(null)
  const [code, setCode] = useState('')
  const [confirmError, setConfirmError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [codes, setCodes] = useState<string[] | null>(null)
  const startedRef = useRef(false)

  // Mint the enrol secret exactly once on mount.
  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true
    ;(async () => {
      try {
        const data = await loginMfaApi.enrollStart(mfaToken)
        setStartData(data)
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          onAbort('Session expired, please sign in again.')
          return
        }
        setStartError(extractMessage(err))
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleConfirm(e: React.FormEvent) {
    e.preventDefault()
    if (!startData) return
    setSubmitting(true)
    setConfirmError(null)
    try {
      const resp = await loginMfaApi.enrollAndVerify(mfaToken, {
        secret_base32: startData.secret_base32,
        code: code.trim(),
      })
      // Drop the plaintext secret as soon as verify succeeds.
      setStartData(null)
      setCodes(resp.backup_codes)
      // Store the full-access token and bootstrap the session. The user will
      // continue to the post-login landing page once they dismiss the
      // backup-codes modal.
      await login(resp.access_token)
      setStep('codes')
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          onAbort('Session expired, please sign in again.')
          return
        }
        if (err.status === 400) {
          setConfirmError('Incorrect code — please try again.')
        } else {
          setConfirmError(extractMessage(err))
        }
      } else {
        setConfirmError(extractMessage(err))
      }
    } finally {
      setSubmitting(false)
    }
  }

  function handleCodesClosed() {
    setCodes(null)
    navigate(nextPath, { replace: true })
  }

  if (step === 'codes' && codes) {
    return <MfaBackupCodesModal codes={codes} onClose={handleCodesClosed} />
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-surface-base">
      <div className="w-full max-w-md">
        <div className="bg-surface-raised rounded-lg shadow-sm border border-border-base p-8">
          <div className="flex items-center gap-2 mb-6">
            <BrandMark size="md" />
            <span className="text-base font-semibold text-content-primary">
              Bulk Loader
            </span>
          </div>

          <h1 className="text-sm font-medium text-content-primary mb-1">
            Set up two-factor authentication
          </h1>
          <p className="text-xs text-content-muted mb-5">
            Your administrator requires 2FA before you can continue. Scan the
            QR code with an authenticator app (1Password, Authy, Google
            Authenticator, etc.) then enter the code it generates.
          </p>

          {startError && (
            <div role="alert" className={`mb-4 ${ALERT_ERROR}`}>
              {startError}
            </div>
          )}

          {!startData && !startError ? (
            <div className="flex justify-center py-10">
              <Spinner />
            </div>
          ) : startData ? (
            <>
              {step === 'scan' && (
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
                  <div className="flex justify-end gap-2">
                    <Button
                      variant="secondary"
                      onClick={() => onAbort()}
                      type="button"
                    >
                      Cancel
                    </Button>
                    <Button
                      variant="primary"
                      onClick={() => setStep('confirm')}
                      type="button"
                    >
                      Next
                    </Button>
                  </div>
                </div>
              )}

              {step === 'confirm' && (
                <form onSubmit={handleConfirm} className="space-y-4">
                  <div>
                    <label htmlFor="login-mfa-enroll-code" className={LABEL_CLASS}>
                      Authenticator code
                    </label>
                    <input
                      id="login-mfa-enroll-code"
                      type="text"
                      inputMode="numeric"
                      pattern="[0-9]*"
                      autoComplete="one-time-code"
                      maxLength={6}
                      value={code}
                      onChange={(e) => {
                        setCode(e.target.value.replace(/\D/g, ''))
                        setConfirmError(null)
                      }}
                      className={INPUT_CLASS + ' font-mono tracking-widest text-center'}
                      placeholder="123456"
                      autoFocus
                      data-testid="mfa-enroll-code"
                    />
                  </div>
                  <div className={ALERT_INFO}>
                    After verification you'll receive 10 one-time backup
                    codes. Save them in a secure place — they let you sign in
                    if you lose access to your authenticator.
                  </div>
                  {confirmError && (
                    <div role="alert" className={ALERT_ERROR}>
                      {confirmError}
                    </div>
                  )}
                  <div className="flex justify-end gap-2">
                    <Button
                      variant="secondary"
                      type="button"
                      onClick={() => setStep('scan')}
                      disabled={submitting}
                    >
                      Back
                    </Button>
                    <Button
                      variant="primary"
                      type="submit"
                      loading={submitting}
                      disabled={submitting || code.trim().length < 6}
                    >
                      Verify and continue
                    </Button>
                  </div>
                </form>
              )}
            </>
          ) : null}
        </div>
      </div>
    </div>
  )
}
