/**
 * PersonalAccessTokensTab — per-user PAT management (SFBL-369).
 *
 * Lists the current user's Personal Access Tokens (metadata only), allows
 * creating new tokens (copy-once plaintext modal), and revoking existing ones.
 *
 * Gate: wrapped in PermissionGate on "tokens.manage" by the parent page —
 * this component itself does not add a second gate.
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import {
  faPlus,
  faKey,
  faCopy,
  faCheck,
  faTriangleExclamation,
} from '@fortawesome/free-solid-svg-icons'
import { Button } from '../../components/ui/Button'
import { Modal } from '../../components/ui/Modal'
import { Badge } from '../../components/ui/Badge'
import { useToast } from '../../components/ui/Toast'
import { patApi } from '../../api/endpoints'
import { ApiError } from '../../api/client'
import { formatApiError } from '../../api/errors'
import type { PatCreateResponse, PatMetadata } from '../../api/types'
import {
  LABEL_CLASS,
  INPUT_CLASS,
  ALERT_WARNING,
} from '../../components/ui/formStyles'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

function tokenStatus(token: PatMetadata): { label: string; variant: 'success' | 'warning' | 'neutral' } {
  if (token.revoked_at) return { label: 'Revoked', variant: 'warning' }
  if (token.expires_at && new Date(token.expires_at) < new Date()) {
    return { label: 'Expired', variant: 'warning' }
  }
  return { label: 'Active', variant: 'success' }
}

// ─── Create form ──────────────────────────────────────────────────────────────

interface CreateFormProps {
  open: boolean
  onClose: () => void
  submitting: boolean
  errorMessage: string | null
  onSubmit: (name: string, expiresAt: string | null) => void
}

function CreateTokenModal({ open, onClose, submitting, errorMessage, onSubmit }: CreateFormProps) {
  const [name, setName] = useState('')
  const [expiresAt, setExpiresAt] = useState('')

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    onSubmit(name.trim(), expiresAt || null)
  }

  function handleClose() {
    setName('')
    setExpiresAt('')
    onClose()
  }

  return (
    <Modal
      open={open}
      onClose={handleClose}
      title="New Personal Access Token"
      size="md"
      closeOnBackdropClick={!submitting}
      footer={
        <>
          <Button variant="secondary" onClick={handleClose} disabled={submitting}>
            Cancel
          </Button>
          <Button
            type="submit"
            form="pat-create-form"
            loading={submitting}
            disabled={!name.trim() || submitting}
          >
            Generate token
          </Button>
        </>
      }
    >
      <form id="pat-create-form" onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor="pat-name" className={LABEL_CLASS}>
            Token name <span className="text-error-text" aria-hidden="true">*</span>
          </label>
          <input
            id="pat-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. CI pipeline, local dev"
            maxLength={255}
            required
            autoFocus
            className={INPUT_CLASS}
          />
          <p className="mt-1 text-xs text-content-muted">
            A memorable label so you can identify this token later.
          </p>
        </div>

        <div>
          <label htmlFor="pat-expires" className={LABEL_CLASS}>
            Expiry date <span className="text-content-muted font-normal">(optional)</span>
          </label>
          <input
            id="pat-expires"
            type="date"
            value={expiresAt}
            onChange={(e) => setExpiresAt(e.target.value)}
            min={new Date().toISOString().slice(0, 10)}
            className={INPUT_CLASS}
          />
          <p className="mt-1 text-xs text-content-muted">
            Leave blank to create a non-expiring token.
          </p>
        </div>

        {errorMessage && (
          <p className="text-sm text-error-text">{errorMessage}</p>
        )}
      </form>
    </Modal>
  )
}

// ─── Copy-once modal ──────────────────────────────────────────────────────────

interface CopyOnceModalProps {
  result: PatCreateResponse | null
  onClose: () => void
}

/**
 * Copy-once modal — shows the plaintext token exactly once.
 *
 * The token is displayed with a copy-to-clipboard button. Closing the modal
 * discards the plaintext from state (it is never stored persistently in the
 * frontend). An unmissable banner tells the user the token will not be shown
 * again.
 *
 * See docs/ui-conventions.md § "Copy-once modal" for the pattern spec.
 */
function CopyOnceModal({ result, onClose }: CopyOnceModalProps) {
  const [copied, setCopied] = useState(false)

  async function handleCopy() {
    if (!result) return
    try {
      await navigator.clipboard.writeText(result.token)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard API not available (non-https context) — silently ignore.
    }
  }

  return (
    <Modal
      open={result !== null}
      onClose={onClose}
      title="Token created"
      size="lg"
      closeOnBackdropClick={false}
      footer={
        <Button onClick={onClose} data-testid="pat-copy-once-done">
          Done — I've copied my token
        </Button>
      }
    >
      <div className="space-y-4">
        {/* Unmissable warning banner */}
        <div className={`${ALERT_WARNING} flex items-start gap-3`} role="alert">
          <FontAwesomeIcon
            icon={faTriangleExclamation}
            className="w-4 h-4 flex-shrink-0 mt-0.5"
            aria-hidden="true"
          />
          <div>
            <p className="font-semibold">This token will not be shown again.</p>
            <p className="text-sm mt-0.5">
              Copy it now and store it securely. Once you close this dialog, there
              is no way to retrieve the token — you will need to revoke it and
              create a new one.
            </p>
          </div>
        </div>

        {/* Token display + copy button */}
        <div>
          <p className="text-sm font-medium text-content-secondary mb-2">
            Personal Access Token for <span className="font-semibold text-content-primary">{result?.name}</span>
          </p>
          <div className="flex items-stretch gap-2">
            <pre
              className="flex-1 rounded-md bg-surface-code text-content-code px-3 py-2 text-xs font-mono whitespace-pre-wrap break-all select-all overflow-x-auto"
              data-testid="pat-plaintext"
              aria-label="Personal access token value"
            >
              {result?.token ?? ''}
            </pre>
            <button
              type="button"
              onClick={handleCopy}
              title={copied ? 'Copied!' : 'Copy to clipboard'}
              aria-label={copied ? 'Copied!' : 'Copy token to clipboard'}
              className="
                flex-shrink-0 flex items-center justify-center w-10 rounded-md
                border border-border-strong bg-surface-raised
                text-content-secondary hover:bg-surface-hover hover:text-content-primary
                focus:outline-none focus:ring-2 focus:ring-border-focus
                transition-colors duration-150
              "
              data-testid="pat-copy-button"
            >
              <FontAwesomeIcon
                icon={copied ? faCheck : faCopy}
                className="w-4 h-4"
                aria-hidden="true"
              />
            </button>
          </div>
          <p className="mt-1.5 text-xs text-content-muted">
            Prefix: <span className="font-mono">{result?.prefix}</span>
            {'  ·  '}
            Last 4: <span className="font-mono">{result?.last4}</span>
          </p>
        </div>
      </div>
    </Modal>
  )
}

// ─── Main tab ─────────────────────────────────────────────────────────────────

export function PersonalAccessTokensTab() {
  const toast = useToast()
  const qc = useQueryClient()

  const [createOpen, setCreateOpen] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [newTokenResult, setNewTokenResult] = useState<PatCreateResponse | null>(null)
  const [revokeTarget, setRevokeTarget] = useState<PatMetadata | null>(null)

  const { data: tokens = [], isLoading } = useQuery({
    queryKey: ['pat-tokens'],
    queryFn: patApi.list,
    staleTime: 30_000,
    retry: 1,
  })

  const invalidate = () => qc.invalidateQueries({ queryKey: ['pat-tokens'] })

  const createMut = useMutation({
    mutationFn: ({ name, expiresAt }: { name: string; expiresAt: string | null }) =>
      patApi.create({
        name,
        expires_at: expiresAt ? new Date(expiresAt).toISOString() : null,
      }),
    onSuccess: (result) => {
      invalidate()
      setCreateOpen(false)
      setCreateError(null)
      setNewTokenResult(result)
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 403) {
        setCreateError('You do not have permission to create tokens, or this action requires session authentication.')
      } else {
        setCreateError(formatApiError(err, 'Failed to create token'))
      }
    },
  })

  const revokeMut = useMutation({
    mutationFn: (id: string) => patApi.revoke(id),
    onSuccess: () => {
      invalidate()
      setRevokeTarget(null)
      toast.success('Token revoked')
    },
    onError: (err) => toast.error(formatApiError(err, 'Failed to revoke token')),
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-content-muted">
          Personal Access Tokens let you authenticate API and MCP requests without using
          your password. Treat them like passwords — store them securely.
        </p>
        <Button onClick={() => { setCreateError(null); setCreateOpen(true) }} data-testid="pat-new-button">
          <FontAwesomeIcon icon={faPlus} className="mr-2" aria-hidden="true" />
          New token
        </Button>
      </div>

      <div className="bg-surface-raised border border-border-base rounded-lg overflow-hidden">
        {isLoading ? (
          <div className="p-8 text-center text-sm text-content-muted">Loading…</div>
        ) : tokens.length === 0 ? (
          <div className="p-8 text-center">
            <FontAwesomeIcon icon={faKey} className="w-8 h-8 text-content-muted mb-3" aria-hidden="true" />
            <p className="text-sm text-content-muted">No tokens yet.</p>
            <p className="text-xs text-content-muted mt-1">
              Create a token to authenticate API or MCP requests.
            </p>
          </div>
        ) : (
          <table className="w-full text-sm" data-testid="pat-table">
            <thead className="bg-surface-sunken text-left text-xs uppercase tracking-wide text-content-muted">
              <tr>
                <th className="px-4 py-2">Name</th>
                <th className="px-4 py-2">Token</th>
                <th className="px-4 py-2">Created</th>
                <th className="px-4 py-2">Last used</th>
                <th className="px-4 py-2">Expires</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-base">
              {tokens.map((token) => {
                const status = tokenStatus(token)
                return (
                  <tr key={token.id} className="hover:bg-surface-hover">
                    <td className="px-4 py-3 font-medium text-content-primary">
                      {token.name}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-content-muted">
                      {token.prefix}…{token.last4}
                    </td>
                    <td className="px-4 py-3 text-content-secondary whitespace-nowrap">
                      {formatDate(token.created_at)}
                    </td>
                    <td className="px-4 py-3 text-content-secondary whitespace-nowrap">
                      {formatDate(token.last_used_at)}
                    </td>
                    <td className="px-4 py-3 text-content-secondary whitespace-nowrap">
                      {formatDate(token.expires_at)}
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant={status.variant}>{status.label}</Badge>
                    </td>
                    <td className="px-4 py-3 text-right">
                      {!token.revoked_at && (
                        <Button
                          size="sm"
                          variant="danger"
                          onClick={() => setRevokeTarget(token)}
                          data-testid={`pat-revoke-${token.id}`}
                        >
                          Revoke
                        </Button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Create form modal */}
      <CreateTokenModal
        open={createOpen}
        onClose={() => { setCreateOpen(false); setCreateError(null) }}
        submitting={createMut.isPending}
        errorMessage={createError}
        onSubmit={(name, expiresAt) => createMut.mutate({ name, expiresAt })}
      />

      {/* Copy-once plaintext modal (shown after successful create) */}
      <CopyOnceModal
        result={newTokenResult}
        onClose={() => setNewTokenResult(null)}
      />

      {/* Revoke confirmation */}
      <Modal
        open={revokeTarget !== null}
        onClose={() => setRevokeTarget(null)}
        title="Revoke token"
        size="sm"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => setRevokeTarget(null)}
              disabled={revokeMut.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              loading={revokeMut.isPending}
              onClick={() => revokeTarget && revokeMut.mutate(revokeTarget.id)}
              data-testid="pat-revoke-confirm"
            >
              Revoke
            </Button>
          </>
        }
      >
        <p className="text-sm text-content-secondary">
          Revoke token{' '}
          <span className="font-semibold text-content-primary">{revokeTarget?.name}</span>
          {' '}(<span className="font-mono text-xs">{revokeTarget?.prefix}…{revokeTarget?.last4}</span>
          )?
        </p>
        <p className="mt-2 text-sm text-content-secondary">
          Any scripts or clients using this token will immediately lose access. This
          cannot be undone.
        </p>
      </Modal>
    </div>
  )
}
