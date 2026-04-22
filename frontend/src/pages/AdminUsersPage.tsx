/**
 * AdminUsersPage — SFBL-201
 *
 * Admin-only page at /admin/users. Lists users with status filter, invite
 * modal, per-user action menu, and one-time-reveal modals for tokens /
 * temp passwords.
 */

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import {
  Button,
  Badge,
  Modal,
  DataTable,
  type Column,
} from '../components/ui'
import { useToast } from '../components/ui/Toast'
import {
  LABEL_CLASS,
  INPUT_CLASS,
  SELECT_CLASS,
  ALERT_ERROR,
  ALERT_WARNING,
  CHECKBOX_CLASS,
} from '../components/ui/formStyles'
import { adminUsersApi } from '../api/endpoints'
import { ApiError } from '../api/client'
import type { AdminUser, ProfileListItem } from '../api/types'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

function extractErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.message) return err.message
  }
  if (err instanceof Error) return err.message
  return 'An unexpected error occurred'
}

function statusVariant(
  status: string,
): 'success' | 'warning' | 'error' | 'info' | 'neutral' {
  switch (status) {
    case 'active':
      return 'success'
    case 'invited':
      return 'info'
    case 'locked':
      return 'warning'
    case 'deactivated':
      return 'neutral'
    case 'deleted':
      return 'error'
    default:
      return 'neutral'
  }
}

function statusLabel(status: string): string {
  switch (status) {
    case 'active':
      return 'Active'
    case 'invited':
      return 'Invited'
    case 'locked':
      return 'Locked'
    case 'deactivated':
      return 'Deactivated'
    case 'deleted':
      return 'Deleted'
    default:
      return status
  }
}

// ─── One-time reveal modal ─────────────────────────────────────────────────────

interface RevealModalProps {
  open: boolean
  title: string
  label: string
  value: string
  warning: string
  onClose: () => void
}

function RevealModal({ open, title, label, value, warning, onClose }: RevealModalProps) {
  const [copied, setCopied] = useState(false)

  function handleCopy() {
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <Modal
      open={open}
      onClose={() => {}}
      closeOnBackdropClick={false}
      title={title}
      size="md"
      footer={
        <Button variant="primary" onClick={onClose}>
          I've saved this — close
        </Button>
      }
    >
      <div className="space-y-4">
        <div className={ALERT_WARNING}>
          <strong>Warning:</strong> {warning}
        </div>
        <div>
          <label className={LABEL_CLASS}>{label}</label>
          <div className="flex items-center gap-2 mt-1">
            <code className="flex-1 px-3 py-2 bg-surface-sunken border border-border-strong rounded text-sm font-mono text-content-primary break-all">
              {value}
            </code>
            <Button variant="secondary" size="sm" onClick={handleCopy}>
              {copied ? 'Copied!' : 'Copy'}
            </Button>
          </div>
        </div>
      </div>
    </Modal>
  )
}

// ─── Invite modal ──────────────────────────────────────────────────────────────

interface InviteModalProps {
  open: boolean
  profiles: ProfileListItem[]
  onClose: () => void
  onSuccess: (token: string, email: string) => void
}

function InviteModal({ open, profiles, onClose, onSuccess }: InviteModalProps) {
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [profileId, setProfileId] = useState('')
  const [error, setError] = useState<string | null>(null)

  const inviteMutation = useMutation({
    mutationFn: () =>
      adminUsersApi.invite({
        email,
        profile_id: profileId,
        display_name: displayName || null,
      }),
    onSuccess: (data) => {
      onSuccess(data.raw_token, data.user.email)
      handleClose()
    },
    onError: (err) => {
      setError(extractErrorMessage(err))
    },
  })

  function handleClose() {
    setEmail('')
    setDisplayName('')
    setProfileId('')
    setError(null)
    onClose()
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!email.trim()) {
      setError('Email is required')
      return
    }
    if (!profileId) {
      setError('Profile is required')
      return
    }
    setError(null)
    inviteMutation.mutate()
  }

  return (
    <Modal
      open={open}
      onClose={handleClose}
      title="Invite User"
      size="md"
      footer={
        <>
          <Button variant="secondary" onClick={handleClose} disabled={inviteMutation.isPending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={handleSubmit}
            loading={inviteMutation.isPending}
          >
            Send Invitation
          </Button>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <div className={ALERT_ERROR}>{error}</div>}
        <div>
          <label htmlFor="invite-email" className={LABEL_CLASS}>
            Email <span className="text-error-text">*</span>
          </label>
          <input
            id="invite-email"
            type="email"
            className={INPUT_CLASS}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="off"
            required
          />
        </div>
        <div>
          <label htmlFor="invite-display-name" className={LABEL_CLASS}>
            Display Name
          </label>
          <input
            id="invite-display-name"
            type="text"
            className={INPUT_CLASS}
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Optional"
          />
        </div>
        <div>
          <label htmlFor="invite-profile" className={LABEL_CLASS}>
            Profile <span className="text-error-text">*</span>
          </label>
          <select
            id="invite-profile"
            className={SELECT_CLASS}
            value={profileId}
            onChange={(e) => setProfileId(e.target.value)}
            required
          >
            <option value="">Select a profile…</option>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.description ? ` — ${p.description}` : ''}
              </option>
            ))}
          </select>
        </div>
      </form>
    </Modal>
  )
}

// ─── Edit user modal ───────────────────────────────────────────────────────────

interface EditModalProps {
  open: boolean
  user: AdminUser | null
  profiles: ProfileListItem[]
  onClose: () => void
  onSuccess: () => void
}

function EditModal({ open, user, profiles, onClose, onSuccess }: EditModalProps) {
  const [profileId, setProfileId] = useState(user?.profile?.id ?? '')
  const [displayName, setDisplayName] = useState(user?.display_name ?? '')
  const [error, setError] = useState<string | null>(null)

  // Sync form when user changes
  const userId = user?.id
  const userProfileId = user?.profile?.id
  const userDisplayName = user?.display_name

  // Reset form when user prop changes
  if (user && userId && (profileId !== (userProfileId ?? '') || displayName !== (userDisplayName ?? ''))) {
    // only reset if modal just opened (no error means first open)
    if (!error) {
      // noop — handled via key prop on parent
    }
  }

  const queryClient = useQueryClient()
  const updateMutation = useMutation({
    mutationFn: () =>
      adminUsersApi.update(user!.id, {
        profile_id: profileId || null,
        display_name: displayName || null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      onSuccess()
      handleClose()
    },
    onError: (err) => {
      setError(extractErrorMessage(err))
    },
  })

  function handleClose() {
    setError(null)
    onClose()
  }

  if (!user) return null

  return (
    <Modal
      open={open}
      onClose={handleClose}
      title={`Edit User — ${user.email}`}
      size="md"
      footer={
        <>
          <Button variant="secondary" onClick={handleClose} disabled={updateMutation.isPending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={() => updateMutation.mutate()}
            loading={updateMutation.isPending}
          >
            Save
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && <div className={ALERT_ERROR}>{error}</div>}
        <div>
          <label htmlFor="edit-display-name" className={LABEL_CLASS}>
            Display Name
          </label>
          <input
            id="edit-display-name"
            type="text"
            className={INPUT_CLASS}
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="edit-profile" className={LABEL_CLASS}>
            Profile
          </label>
          <select
            id="edit-profile"
            className={SELECT_CLASS}
            value={profileId}
            onChange={(e) => setProfileId(e.target.value)}
          >
            <option value="">No profile</option>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
      </div>
    </Modal>
  )
}

// ─── Confirm action modal ─────────────────────────────────────────────────────

interface ConfirmModalProps {
  open: boolean
  title: string
  message: string
  confirmLabel: string
  variant?: 'primary' | 'danger'
  loading?: boolean
  error?: string | null
  onConfirm: () => void
  onClose: () => void
}

function ConfirmModal({
  open,
  title,
  message,
  confirmLabel,
  variant = 'danger',
  loading,
  error,
  onConfirm,
  onClose,
}: ConfirmModalProps) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={loading}>
            Cancel
          </Button>
          <Button variant={variant} onClick={onConfirm} loading={loading}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {error && <div className={clsx(ALERT_ERROR, 'mb-3')}>{error}</div>}
      <p className="text-sm text-content-secondary">{message}</p>
    </Modal>
  )
}

// ─── User actions menu ─────────────────────────────────────────────────────────

type ActionType =
  | 'edit'
  | 'deactivate'
  | 'reactivate'
  | 'unlock'
  | 'reset-password'
  | 'resend-invite'
  | 'delete'

interface ActionsMenuProps {
  user: AdminUser
  onAction: (action: ActionType, user: AdminUser) => void
}

function ActionsMenu({ user, onAction }: ActionsMenuProps) {
  const [open, setOpen] = useState(false)

  const actions: { label: string; action: ActionType; show: boolean }[] = [
    { label: 'Edit', action: 'edit', show: user.status !== 'deleted' },
    {
      label: 'Deactivate',
      action: 'deactivate',
      show: user.status === 'active',
    },
    {
      label: 'Reactivate',
      action: 'reactivate',
      show: user.status === 'deactivated',
    },
    {
      label: 'Unlock',
      action: 'unlock',
      show: user.status === 'locked',
    },
    {
      label: 'Reset Password',
      action: 'reset-password',
      show: user.status !== 'deleted' && user.status !== 'invited',
    },
    {
      label: 'Resend Invitation',
      action: 'resend-invite',
      show: user.status === 'invited',
    },
    {
      label: 'Delete',
      action: 'delete',
      show: user.status !== 'deleted',
    },
  ]

  const visibleActions = actions.filter((a) => a.show)

  return (
    <div className="relative">
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setOpen((v) => !v)}
        aria-label={`Actions for ${user.email}`}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        ···
      </Button>
      {open && (
        <>
          {/* Backdrop to close menu */}
          <div
            className="fixed inset-0 z-10"
            onClick={() => setOpen(false)}
            aria-hidden="true"
          />
          <div className="absolute right-0 mt-1 bg-surface-elevated border border-border-base rounded-md shadow-lg z-20 min-w-[160px]">
            {visibleActions.map((a) => (
              <button
                key={a.action}
                className={clsx(
                  'w-full text-left px-4 py-2 text-sm transition-colors',
                  a.action === 'delete'
                    ? 'text-error-text hover:bg-error-bg'
                    : 'text-content-secondary hover:bg-surface-hover',
                )}
                role="menuitem"
                onClick={() => {
                  setOpen(false)
                  onAction(a.action, user)
                }}
              >
                {a.label}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

// ─── Status filter bar ────────────────────────────────────────────────────────

const STATUS_FILTERS = ['all', 'active', 'invited', 'locked', 'deactivated'] as const
type StatusFilter = (typeof STATUS_FILTERS)[number]

// ─── Main page ────────────────────────────────────────────────────────────────

export default function AdminUsersPage() {
  const queryClient = useQueryClient()
  const toast = useToast()

  // Filter state
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [includeDeleted, setIncludeDeleted] = useState(false)

  // Modal state
  const [inviteOpen, setInviteOpen] = useState(false)
  const [editUser, setEditUser] = useState<AdminUser | null>(null)
  const [revealToken, setRevealToken] = useState<{ token: string; email: string } | null>(null)
  const [revealPassword, setRevealPassword] = useState<string | null>(null)
  const [confirmAction, setConfirmAction] = useState<{
    action: ActionType
    user: AdminUser
    error: string | null
  } | null>(null)

  // Queries
  const { data: usersData, isLoading: usersLoading } = useQuery({
    queryKey: ['admin-users', statusFilter, includeDeleted],
    queryFn: () =>
      adminUsersApi.list({
        status: statusFilter !== 'all' ? statusFilter : undefined,
        include_deleted: includeDeleted,
        page_size: 100,
      }),
  })

  const { data: profiles = [] } = useQuery({
    queryKey: ['admin-profiles'],
    queryFn: () => adminUsersApi.listProfiles(),
  })

  // Action mutation
  const actionMutation = useMutation({
    mutationFn: async ({ action, user }: { action: ActionType; user: AdminUser }) => {
      switch (action) {
        case 'deactivate':
          return adminUsersApi.deactivate(user.id)
        case 'reactivate':
          return adminUsersApi.reactivate(user.id)
        case 'unlock':
          return adminUsersApi.unlock(user.id)
        case 'reset-password':
          return adminUsersApi.resetPassword(user.id)
        case 'resend-invite':
          return adminUsersApi.resendInvite(user.id)
        case 'delete':
          return adminUsersApi.delete(user.id)
        default:
          return null
      }
    },
    onSuccess: (data, { action, user }) => {
      queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      setConfirmAction(null)

      if (action === 'reset-password' && data && 'temp_password' in data) {
        setRevealPassword((data as { temp_password: string }).temp_password)
      } else if (action === 'resend-invite' && data && 'raw_token' in data) {
        setRevealToken({
          token: (data as { raw_token: string }).raw_token,
          email: user.email,
        })
      } else {
        const labels: Record<string, string> = {
          deactivate: 'User deactivated',
          reactivate: 'User reactivated',
          unlock: 'User unlocked',
          delete: 'User deleted',
        }
        toast.success(labels[action] ?? 'Action completed')
      }
    },
    onError: (err) => {
      if (confirmAction) {
        setConfirmAction({ ...confirmAction, error: extractErrorMessage(err) })
      } else {
        toast.error(extractErrorMessage(err))
      }
    },
  })

  function handleAction(action: ActionType, user: AdminUser) {
    if (action === 'edit') {
      setEditUser(user)
      return
    }
    setConfirmAction({ action, user, error: null })
  }

  function handleInviteSuccess(token: string, email: string) {
    queryClient.invalidateQueries({ queryKey: ['admin-users'] })
    setRevealToken({ token, email })
  }

  const confirmMessages: Record<
    ActionType,
    (u: AdminUser) => { title: string; message: string; confirmLabel: string; variant: 'primary' | 'danger' }
  > = {
    edit: () => ({ title: '', message: '', confirmLabel: '', variant: 'primary' }),
    deactivate: (u) => ({
      title: 'Deactivate User',
      message: `Deactivate ${u.email}? They will no longer be able to log in.`,
      confirmLabel: 'Deactivate',
      variant: 'danger',
    }),
    reactivate: (u) => ({
      title: 'Reactivate User',
      message: `Reactivate ${u.email}? They will be able to log in again.`,
      confirmLabel: 'Reactivate',
      variant: 'primary',
    }),
    unlock: (u) => ({
      title: 'Unlock Account',
      message: `Unlock ${u.email}'s account? This clears their lockout.`,
      confirmLabel: 'Unlock',
      variant: 'primary',
    }),
    'reset-password': (u) => ({
      title: 'Reset Password',
      message: `Issue a temporary password for ${u.email}? Their current password will be invalidated and they must reset it on next login.`,
      confirmLabel: 'Reset Password',
      variant: 'danger',
    }),
    'resend-invite': (u) => ({
      title: 'Resend Invitation',
      message: `Issue a new invitation token for ${u.email}?`,
      confirmLabel: 'Resend',
      variant: 'primary',
    }),
    delete: (u) => ({
      title: 'Delete User',
      message: `Delete ${u.email}? This is a soft delete — the account will be tombstoned but the email can be re-used.`,
      confirmLabel: 'Delete',
      variant: 'danger',
    }),
  }

  const columns: Column<AdminUser>[] = [
    {
      key: 'email',
      header: 'Email',
      render: (u) => (
        <span className="font-medium text-content-primary">{u.email}</span>
      ),
    },
    {
      key: 'display_name',
      header: 'Display Name',
      render: (u) => (
        <span className="text-content-secondary">{u.display_name ?? '—'}</span>
      ),
    },
    {
      key: 'profile',
      header: 'Profile',
      render: (u) =>
        u.profile ? (
          <Badge variant="neutral">{u.profile.name}</Badge>
        ) : (
          <span className="text-content-muted text-xs">—</span>
        ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (u) => (
        <Badge variant={statusVariant(u.status)} dot>
          {statusLabel(u.status)}
        </Badge>
      ),
    },
    {
      key: 'last_login_at',
      header: 'Last Login',
      render: (u) => (
        <span className="text-content-secondary text-xs">{formatDate(u.last_login_at)}</span>
      ),
    },
    {
      key: 'actions',
      header: '',
      render: (u) => <ActionsMenu user={u} onAction={handleAction} />,
      className: 'text-right',
      headerClassName: 'text-right',
    },
  ]

  const confirmInfo =
    confirmAction && confirmAction.action !== 'edit'
      ? confirmMessages[confirmAction.action](confirmAction.user)
      : null

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-content-primary">User Management</h1>
          <p className="mt-1 text-sm text-content-muted">
            Manage user accounts, profiles, and invitations.
          </p>
        </div>
        <Button variant="primary" onClick={() => setInviteOpen(true)}>
          Invite User
        </Button>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-4 flex-wrap">
        {/* Status chips */}
        <div className="flex items-center gap-1">
          {STATUS_FILTERS.map((s) => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className={clsx(
                'px-3 py-1.5 rounded-full text-xs font-medium transition-colors border',
                statusFilter === s
                  ? 'bg-accent text-content-inverse border-accent'
                  : 'bg-surface-raised text-content-secondary border-border-base hover:bg-surface-hover',
              )}
            >
              {s === 'all' ? 'All' : statusLabel(s)}
            </button>
          ))}
        </div>

        {/* Include deleted toggle */}
        <label className="flex items-center gap-2 text-sm text-content-secondary cursor-pointer select-none">
          <input
            type="checkbox"
            className={CHECKBOX_CLASS}
            checked={includeDeleted}
            onChange={(e) => setIncludeDeleted(e.target.checked)}
          />
          Show deleted
        </label>

        {/* Total count */}
        {usersData && (
          <span className="text-xs text-content-muted ml-auto">
            {usersData.total} user{usersData.total !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      {/* Table */}
      <div className="border border-border-base rounded-lg overflow-hidden">
        <DataTable
          columns={columns}
          data={usersData?.items ?? []}
          keyExtractor={(u) => u.id}
          loading={usersLoading}
          emptyMessage="No users found."
        />
      </div>

      {/* Modals */}
      <InviteModal
        open={inviteOpen}
        profiles={profiles}
        onClose={() => setInviteOpen(false)}
        onSuccess={handleInviteSuccess}
      />

      {editUser && (
        <EditModal
          key={editUser.id}
          open={!!editUser}
          user={editUser}
          profiles={profiles}
          onClose={() => setEditUser(null)}
          onSuccess={() => {
            toast.success('User updated')
          }}
        />
      )}

      {confirmAction && confirmInfo && (
        <ConfirmModal
          open
          title={confirmInfo.title}
          message={confirmInfo.message}
          confirmLabel={confirmInfo.confirmLabel}
          variant={confirmInfo.variant}
          loading={actionMutation.isPending}
          error={confirmAction.error}
          onConfirm={() =>
            actionMutation.mutate({ action: confirmAction.action, user: confirmAction.user })
          }
          onClose={() => setConfirmAction(null)}
        />
      )}

      {revealToken && (
        <RevealModal
          open
          title="Invitation Token"
          label="Invitation token (raw)"
          value={revealToken.token}
          warning={`This invitation token for ${revealToken.email} is shown once and cannot be retrieved again. Copy it now to send the invitation link.`}
          onClose={() => setRevealToken(null)}
        />
      )}

      {revealPassword && (
        <RevealModal
          open
          title="Temporary Password"
          label="Temporary password"
          value={revealPassword}
          warning="This temporary password is shown once and cannot be retrieved again. The user must change it on next login."
          onClose={() => setRevealPassword(null)}
        />
      )}
    </div>
  )
}
