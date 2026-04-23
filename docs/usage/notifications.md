---
title: Notifications
slug: notifications
nav_order: 90
tags: [notifications, email, webhook]
summary: >-
  Subscribe to run-completion events via email or webhook (hosted profiles
  only).
---

# Notifications

## What this covers / who should read this

How to get pinged when a run reaches a terminal state, so you don't have to
camp on the Runs page. Available in **hosted profiles** only (`self_hosted`,
`aws_hosted`) — the desktop profile has no user identity to attach
subscriptions to.

Anyone who can see a plan can subscribe themselves to notifications for it.
Managing other users' subscriptions is not currently exposed.

---

## Channels

| Channel | Payload |
|---|---|
| **Email** | Plain-text summary rendered from the `notifications/run_complete` template. Subject line includes plan name + terminal status. |
| **Webhook** | JSON POST to an `https://` URL. Compatible with Slack incoming webhooks. |

### Webhook payload shape

```json
{
  "text": "Accounts Plan: completed_with_errors",
  "run": {
    "run_id": "…",
    "load_plan_id": "…",
    "plan_name": "Accounts Plan",
    "status": "completed_with_errors",
    "started_at": "2026-04-20T12:00:00Z",
    "finished_at": "2026-04-20T12:04:21Z"
  }
}
```

`http://` URLs are rejected. Webhook delivery retries up to 3 times on 5xx,
429, or network errors with exponential backoff + jitter. `4xx` responses are
terminal (no retry).

---

## Triggers

| Trigger | Fires on |
|---|---|
| **Any terminal state** | `completed`, `completed_with_errors`, `failed`, `aborted`. |
| **Failures only** | `completed_with_errors`, `failed`, `aborted`. |

A terminal-state transition fires exactly one notification per matching
subscription.

---

## Subscribing

Two entry points:

### 1. Settings → Notifications (full CRUD)

Pick a channel, destination, plan scope (**specific plan** or **all plans**),
and trigger. Use **Test** to fire a synthetic payload through the real
dispatcher without waiting for a run.

### 2. Plan editor → "Notify me" button

One-click subscription for the current user's email, scoped to the plan
you're editing, with the "any terminal" trigger. After subscribing the button
flips to **Notifications on** with a menu to Edit or Unsubscribe.
**Customize…** opens the Settings form pre-filled with the plan.

---

## Validating a webhook before subscribing

Confirm your endpoint accepts the payload with `curl`:

```bash
curl -X POST https://hooks.example.com/services/T/B/X \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "Accounts Plan: completed",
    "run": {
      "run_id": "00000000-0000-0000-0000-000000000000",
      "load_plan_id": "00000000-0000-0000-0000-000000000000",
      "plan_name": "Accounts Plan",
      "status": "completed",
      "started_at": "2026-04-20T12:00:00Z",
      "finished_at": "2026-04-20T12:01:00Z"
    }
  }'
```

If the endpoint returns 2xx, the in-UI **Test** will also succeed.

---

## Delivery audit trail

Every dispatch (real or test) writes one row to `notification_delivery`
capturing channel, status, attempt count, last error, and — for email — the
linked `email_delivery` row. Test sends carry `is_test=TRUE` and
`run_id=NULL`.

Admins can inspect delivery history via the database directly; there's no
dedicated UI yet.

---

## Email configuration (for admins)

Email delivery requires an SMTP backend configured via environment variables.
See [Docker deployment → Email settings](../deployment/docker.md) for
`EMAIL_BACKEND`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, etc.

---

## Related

- [Running a load](running-loads.md)
- Deployment: [Docker email env vars](../deployment/docker.md)
- Architecture: [Run execution](../architecture/run-execution.md)
