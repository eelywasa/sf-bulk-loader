/**
 * Shared add/edit modal for notification subscriptions.
 *
 * Used by:
 *  - Settings → Notifications tab ("Add subscription" and inline edit)
 *  - PlanEditor toolbar → "Notify me" → "Customize…"
 *
 * Performs light client-side validation; the server is authoritative.
 */

import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, Modal } from '../../components/ui'
import {
  LABEL_CLASS,
  INPUT_CLASS,
  SELECT_CLASS,
  ALERT_ERROR,
} from '../../components/ui/formStyles'
import { plansApi } from '../../api/endpoints'
import type {
  NotificationChannel,
  NotificationSubscription,
  NotificationSubscriptionCreate,
  NotificationTrigger,
} from '../../api/types'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export interface SubscriptionFormModalProps {
  open: boolean
  onClose: () => void
  /** When provided, the modal is in edit mode. */
  editing?: NotificationSubscription | null
  /** Pre-filled plan_id (used for "Customize from plan" entry point). */
  defaultPlanId?: string | null
  /** Lock the plan_id field (non-editable). */
  lockPlanId?: boolean
  submitting: boolean
  errorMessage?: string | null
  onSubmit: (data: NotificationSubscriptionCreate) => void
}

export function SubscriptionFormModal({
  open,
  onClose,
  editing,
  defaultPlanId = null,
  lockPlanId = false,
  submitting,
  errorMessage,
  onSubmit,
}: SubscriptionFormModalProps) {
  const [channel, setChannel] = useState<NotificationChannel>('email')
  const [destination, setDestination] = useState('')
  const [planId, setPlanId] = useState<string | null>(null)
  const [trigger, setTrigger] = useState<NotificationTrigger>('terminal_any')

  const { data: plans } = useQuery({
    queryKey: ['plans'],
    queryFn: plansApi.list,
    staleTime: 30_000,
    enabled: open,
  })

  useEffect(() => {
    if (!open) return
    if (editing) {
      setChannel(editing.channel)
      setDestination(editing.destination)
      setPlanId(editing.plan_id)
      setTrigger(editing.trigger)
    } else {
      setChannel('email')
      setDestination('')
      setPlanId(defaultPlanId)
      setTrigger('terminal_any')
    }
  }, [open, editing, defaultPlanId])

  const destValid =
    channel === 'email'
      ? EMAIL_RE.test(destination.trim())
      : /^https:\/\/.+/i.test(destination.trim())

  function handleSave(e: React.FormEvent) {
    e.preventDefault()
    if (!destValid) return
    onSubmit({
      plan_id: planId,
      channel,
      destination: destination.trim(),
      trigger,
    })
  }

  const title = editing ? 'Edit subscription' : 'Add subscription'

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      size="md"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button
            type="submit"
            form="subscription-form"
            loading={submitting}
            disabled={!destValid || submitting}
          >
            Save
          </Button>
        </>
      }
    >
      <form id="subscription-form" onSubmit={handleSave} className="space-y-4">
        <div>
          <label className={LABEL_CLASS} htmlFor="sub-channel">
            Channel
          </label>
          <select
            id="sub-channel"
            className={SELECT_CLASS}
            value={channel}
            onChange={(e) => setChannel(e.target.value as NotificationChannel)}
          >
            <option value="email">Email</option>
            <option value="webhook">Webhook</option>
          </select>
        </div>

        <div>
          <label className={LABEL_CLASS} htmlFor="sub-destination">
            {channel === 'email' ? 'Email address' : 'Webhook URL'}
          </label>
          <input
            id="sub-destination"
            className={INPUT_CLASS}
            value={destination}
            onChange={(e) => setDestination(e.target.value)}
            placeholder={
              channel === 'email' ? 'you@example.com' : 'https://hooks.example.com/…'
            }
            autoComplete="off"
          />
          {!destValid && destination.length > 0 && (
            <p className="mt-1 text-xs text-red-600">
              {channel === 'email'
                ? 'Enter a valid email address.'
                : 'Webhook URL must start with https://'}
            </p>
          )}
        </div>

        <div>
          <label className={LABEL_CLASS} htmlFor="sub-plan">
            Plan
          </label>
          <select
            id="sub-plan"
            className={SELECT_CLASS}
            value={planId ?? ''}
            onChange={(e) => setPlanId(e.target.value || null)}
            disabled={lockPlanId}
          >
            <option value="">All plans</option>
            {(plans ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>

        <div>
          <span className={LABEL_CLASS}>Trigger</span>
          <div className="mt-1 space-y-2 text-sm text-content-primary">
            <label className="flex items-start gap-2">
              <input
                type="radio"
                name="sub-trigger"
                value="terminal_any"
                checked={trigger === 'terminal_any'}
                onChange={() => setTrigger('terminal_any')}
              />
              <span>
                <span className="font-medium">Any terminal state</span>
                <span className="block text-xs text-content-muted">
                  Fires on success, partial failure, failure, or abort.
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2">
              <input
                type="radio"
                name="sub-trigger"
                value="terminal_fail_only"
                checked={trigger === 'terminal_fail_only'}
                onChange={() => setTrigger('terminal_fail_only')}
              />
              <span>
                <span className="font-medium">Failures only</span>
                <span className="block text-xs text-content-muted">
                  Fires only on failed, aborted, or completed-with-errors.
                </span>
              </span>
            </label>
          </div>
        </div>

        {errorMessage && <div className={ALERT_ERROR}>{errorMessage}</div>}
      </form>
    </Modal>
  )
}
