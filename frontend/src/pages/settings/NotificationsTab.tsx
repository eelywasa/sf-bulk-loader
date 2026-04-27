/**
 * Notifications tab — per-user subscription management (SFBL-183).
 *
 * Hidden on desktop profile (auth_mode=none).
 */

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import { faEnvelope, faLink, faPlus } from '@fortawesome/free-solid-svg-icons'
import { Button } from '../../components/ui/Button'
import { Modal } from '../../components/ui/Modal'
import { Badge } from '../../components/ui/Badge'
import { useToast } from '../../components/ui/Toast'
import { notificationSubscriptionsApi, plansApi } from '../../api/endpoints'
import { ApiError } from '../../api/client'
import { formatApiError } from '../../api/errors'
import type {
  NotificationSubscription,
  NotificationSubscriptionCreate,
  NotificationTrigger,
} from '../../api/types'
import { SubscriptionFormModal } from './SubscriptionFormModal'

function sanitizeUrl(raw: string): string {
  try {
    const u = new URL(raw)
    u.username = ''
    u.password = ''
    u.search = ''
    u.hash = ''
    return u.toString()
  } catch {
    return raw
  }
}

function displayDestination(sub: NotificationSubscription): string {
  return sub.channel === 'webhook' ? sanitizeUrl(sub.destination) : sub.destination
}

function triggerLabel(t: NotificationTrigger): string {
  return t === 'terminal_any' ? 'Any terminal' : 'Failures only'
}

export function NotificationsTab() {
  const toast = useToast()
  const qc = useQueryClient()

  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<NotificationSubscription | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<NotificationSubscription | null>(null)

  const { data: subs = [], isLoading } = useQuery({
    queryKey: ['notification-subscriptions'],
    queryFn: notificationSubscriptionsApi.list,
  })

  const { data: plans = [] } = useQuery({
    queryKey: ['plans'],
    queryFn: plansApi.list,
    staleTime: 30_000,
  })

  const planNameById = useMemo(() => {
    const m = new Map<string, string>()
    for (const p of plans) m.set(p.id, p.name)
    return m
  }, [plans])

  const invalidate = () =>
    qc.invalidateQueries({ queryKey: ['notification-subscriptions'] })

  const createMut = useMutation({
    mutationFn: (data: NotificationSubscriptionCreate) =>
      notificationSubscriptionsApi.create(data),
    onSuccess: () => {
      invalidate()
      setModalOpen(false)
      setEditing(null)
      setFormError(null)
      toast.success('Subscription added')
    },
    onError: (err) => setFormError(formatApiError(err, 'Failed to save')),
  })

  const updateMut = useMutation({
    mutationFn: ({ id, data }: { id: string; data: NotificationSubscriptionCreate }) =>
      notificationSubscriptionsApi.update(id, data),
    onSuccess: () => {
      invalidate()
      setModalOpen(false)
      setEditing(null)
      setFormError(null)
      toast.success('Subscription updated')
    },
    onError: (err) => setFormError(formatApiError(err, 'Failed to save')),
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) => notificationSubscriptionsApi.delete(id),
    onSuccess: () => {
      invalidate()
      setDeleteTarget(null)
      toast.success('Subscription deleted')
    },
    onError: (err) =>
      toast.error(formatApiError(err, 'Failed to delete subscription')),
  })

  const testMut = useMutation({
    mutationFn: (id: string) => notificationSubscriptionsApi.test(id),
    onSuccess: (res) => {
      if (res.status === 'sent' || res.status === 'skipped') {
        toast.success('Test notification dispatched')
      } else if (res.status === 'failed') {
        toast.error(`Test failed: ${res.last_error ?? 'unknown error'}`)
      } else {
        toast.info(`Test ${res.status}`)
      }
    },
    onError: (err) => {
      toast.error(formatApiError(err, 'Test failed'))
    },
  })

  function handleSubmit(data: NotificationSubscriptionCreate) {
    if (editing) updateMut.mutate({ id: editing.id, data })
    else createMut.mutate(data)
  }

  function openAdd() {
    setEditing(null)
    setFormError(null)
    setModalOpen(true)
  }

  function openEdit(sub: NotificationSubscription) {
    setEditing(sub)
    setFormError(null)
    setModalOpen(true)
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-content-muted">
          Get notified when a run reaches a terminal state. Subscribe to a specific plan
          or to all plans.
        </p>
        <Button onClick={openAdd}>
          <FontAwesomeIcon icon={faPlus} className="mr-2" aria-hidden="true" />
          Add subscription
        </Button>
      </div>

      <div className="bg-surface-raised border border-border-base rounded-lg overflow-hidden">
        {isLoading ? (
          <div className="p-8 text-center text-sm text-content-muted">Loading…</div>
        ) : subs.length === 0 ? (
          <div className="p-8 text-center text-sm text-content-muted">
            No subscriptions yet.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-surface-sunken text-left text-xs uppercase tracking-wide text-content-muted">
              <tr>
                <th className="px-4 py-2 w-10" aria-label="Channel" />
                <th className="px-4 py-2">Destination</th>
                <th className="px-4 py-2">Plan</th>
                <th className="px-4 py-2">Trigger</th>
                <th className="px-4 py-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-base">
              {subs.map((sub) => (
                <tr key={sub.id}>
                  <td className="px-4 py-3 text-content-muted">
                    <FontAwesomeIcon
                      icon={sub.channel === 'email' ? faEnvelope : faLink}
                      aria-label={sub.channel}
                    />
                  </td>
                  <td className="px-4 py-3 font-mono text-xs break-all text-content-primary">
                    {displayDestination(sub)}
                  </td>
                  <td className="px-4 py-3 text-content-secondary">
                    {sub.plan_id ? (
                      planNameById.get(sub.plan_id) ?? (
                        <span className="text-content-muted">
                          (plan {sub.plan_id.slice(0, 8)})
                        </span>
                      )
                    ) : (
                      <Badge variant="info">All plans</Badge>
                    )}
                  </td>
                  <td className="px-4 py-3 text-content-secondary">
                    {triggerLabel(sub.trigger)}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-2">
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => testMut.mutate(sub.id)}
                        loading={testMut.isPending && testMut.variables === sub.id}
                      >
                        Test
                      </Button>
                      <Button size="sm" variant="secondary" onClick={() => openEdit(sub)}>
                        Edit
                      </Button>
                      <Button
                        size="sm"
                        variant="danger"
                        onClick={() => setDeleteTarget(sub)}
                      >
                        Delete
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <SubscriptionFormModal
        open={modalOpen}
        editing={editing}
        onClose={() => {
          setModalOpen(false)
          setEditing(null)
          setFormError(null)
        }}
        submitting={createMut.isPending || updateMut.isPending}
        errorMessage={formError}
        onSubmit={handleSubmit}
      />

      <Modal
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        title="Delete subscription"
        size="sm"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => setDeleteTarget(null)}
              disabled={deleteMut.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              loading={deleteMut.isPending}
              onClick={() => deleteTarget && deleteMut.mutate(deleteTarget.id)}
            >
              Delete
            </Button>
          </>
        }
      >
        <p className="text-sm text-content-secondary">
          Delete subscription for{' '}
          <span className="font-mono">
            {deleteTarget ? displayDestination(deleteTarget) : ''}
          </span>
          ? This cannot be undone.
        </p>
      </Modal>
    </div>
  )
}
