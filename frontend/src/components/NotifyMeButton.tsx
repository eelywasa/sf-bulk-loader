/**
 * "Notify me" split-button for the PlanEditor toolbar (SFBL-183).
 *
 * Primary action: quick-subscribe the current user to this plan using the
 * email channel, terminal_any trigger, and the user's email as destination.
 * If a matching subscription already exists, the primary label flips to
 * "Notifications on" and the menu offers Edit / Unsubscribe.
 *
 * Secondary action: "Customize…" opens the shared subscription form modal
 * pre-filled with plan_id.
 *
 * Hidden entirely on desktop profile (auth_mode=none) — callers must not
 * render this component in that case.
 */

import { Fragment, useState } from 'react'
import { Menu, MenuButton, MenuItem, MenuItems } from '@headlessui/react'
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome'
import { faBell, faCaretDown } from '@fortawesome/free-solid-svg-icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'
import { Button } from './ui/Button'
import { OVERLAY_SHADOW_CLASS } from './ui/formStyles'
import { useToast } from './ui/Toast'
import { useAuth } from '../context/AuthContext'
import { notificationSubscriptionsApi } from '../api/endpoints'
import type {
  NotificationSubscription,
  NotificationSubscriptionCreate,
} from '../api/types'
import { SubscriptionFormModal } from '../pages/settings/SubscriptionFormModal'

export interface NotifyMeButtonProps {
  planId: string
}

export function NotifyMeButton({ planId }: NotifyMeButtonProps) {
  const { user } = useAuth()
  const toast = useToast()
  const qc = useQueryClient()

  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<NotificationSubscription | null>(null)
  const [formError, setFormError] = useState<string | null>(null)

  const { data: subs = [] } = useQuery({
    queryKey: ['notification-subscriptions'],
    queryFn: notificationSubscriptionsApi.list,
  })

  // Match current user's email-channel subscription scoped to this plan.
  const existing = subs.find(
    (s) =>
      s.plan_id === planId &&
      s.channel === 'email' &&
      (user?.email ? s.destination === user.email : true),
  )

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
      toast.success('Notifications enabled for this plan')
    },
    onError: (err) => {
      const msg = err instanceof Error ? err.message : 'Failed to subscribe'
      if (modalOpen) setFormError(msg)
      else toast.error(msg)
    },
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
    onError: (err) =>
      setFormError(err instanceof Error ? err.message : 'Failed to save'),
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) => notificationSubscriptionsApi.delete(id),
    onSuccess: () => {
      invalidate()
      toast.success('Unsubscribed')
    },
    onError: (err) =>
      toast.error(err instanceof Error ? err.message : 'Failed to unsubscribe'),
  })

  function handleQuickSubscribe() {
    if (!user?.email) {
      // No email on account — fall back to the customize form.
      setEditing(null)
      setFormError(null)
      setModalOpen(true)
      return
    }
    createMut.mutate({
      plan_id: planId,
      channel: 'email',
      destination: user.email,
      trigger: 'terminal_any',
    })
  }

  function handleCustomize() {
    setEditing(null)
    setFormError(null)
    setModalOpen(true)
  }

  function handleEdit() {
    if (!existing) return
    setEditing(existing)
    setFormError(null)
    setModalOpen(true)
  }

  function handleUnsubscribe() {
    if (!existing) return
    deleteMut.mutate(existing.id)
  }

  function handleModalSubmit(data: NotificationSubscriptionCreate) {
    if (editing) updateMut.mutate({ id: editing.id, data })
    else createMut.mutate(data)
  }

  const isOn = existing !== undefined
  const primaryLabel = isOn ? 'Notifications on' : 'Notify me'
  const primaryAction = isOn ? handleEdit : handleQuickSubscribe
  const primaryDisabled = createMut.isPending || deleteMut.isPending

  // Match the BUTTON_SECONDARY_COLORS surface so the split-button reads as
  // one control. `self-stretch` makes the chevron column inherit the primary
  // button's height regardless of its own padding, so the two halves always
  // share a visual baseline. Render <Menu> as a Fragment (no wrapper div) so
  // the MenuButton sits directly inside the inline-flex parent and is flush
  // against the primary button — MenuItems uses `anchor="bottom end"` and
  // therefore needs no positioning context from the wrapper.
  const menuBtnBase =
    'inline-flex items-center self-stretch px-2.5 text-sm font-medium border border-l-0 border-border-strong rounded-r-md bg-surface-raised hover:bg-surface-hover text-content-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-border-focus disabled:opacity-50 disabled:cursor-not-allowed transition-colors duration-150'

  return (
    <>
      <div className="inline-flex">
        <Button
          variant="secondary"
          onClick={primaryAction}
          loading={primaryDisabled}
          className="!rounded-r-none"
        >
          <FontAwesomeIcon icon={faBell} className="mr-2" aria-hidden="true" />
          {primaryLabel}
        </Button>
        <Menu as={Fragment}>
          <MenuButton className={menuBtnBase} aria-label="Notification options">
            <FontAwesomeIcon icon={faCaretDown} aria-hidden="true" />
          </MenuButton>
          <MenuItems
            anchor="bottom end"
            className={clsx(
              'z-50 mt-1 w-48 rounded-md border border-border-base bg-surface-elevated focus:outline-none',
              OVERLAY_SHADOW_CLASS,
            )}
          >
            {isOn ? (
              <Fragment>
                <MenuItem>
                  {({ focus }: { focus: boolean }) => (
                    <button
                      onClick={handleEdit}
                      className={clsx(
                        'block w-full px-3 py-2 text-left text-sm text-content-primary',
                        focus && 'bg-surface-sunken',
                      )}
                    >
                      Edit subscription…
                    </button>
                  )}
                </MenuItem>
                <MenuItem>
                  {({ focus }: { focus: boolean }) => (
                    <button
                      onClick={handleUnsubscribe}
                      className={clsx(
                        'block w-full px-3 py-2 text-left text-sm text-red-600',
                        focus && 'bg-surface-sunken',
                      )}
                    >
                      Unsubscribe
                    </button>
                  )}
                </MenuItem>
              </Fragment>
            ) : (
              <MenuItem>
                {({ focus }: { focus: boolean }) => (
                  <button
                    onClick={handleCustomize}
                    className={clsx(
                      'block w-full px-3 py-2 text-left text-sm text-content-primary',
                      focus && 'bg-surface-sunken',
                    )}
                  >
                    Customize…
                  </button>
                )}
              </MenuItem>
            )}
          </MenuItems>
        </Menu>
      </div>

      <SubscriptionFormModal
        open={modalOpen}
        editing={editing}
        defaultPlanId={planId}
        lockPlanId={!editing}
        onClose={() => {
          setModalOpen(false)
          setEditing(null)
          setFormError(null)
        }}
        submitting={createMut.isPending || updateMut.isPending}
        errorMessage={formError}
        onSubmit={handleModalSubmit}
      />
    </>
  )
}
