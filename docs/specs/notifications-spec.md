# Spec: Run-Complete Notifications

Status: **Implemented** (SFBL-117, April 2026).

## Overview

Fire a message to the user when a load run reaches a terminal state. Two
channels land in MVP: **email** (reusing the existing `EmailService`) and
**webhook** (JSON POST, Slack-compatible envelope). Subscriptions are
per-user, optionally scoped to a specific plan, and triggered by either
"any terminal state" or "failures only".

## Decisions (locked)

- **D1 — Subscription visibility (MVP)**: no plan-ownership check. Any
  authenticated user may subscribe to any plan's notifications. To be
  revisited when RBAC lands.
- **D2 — Trigger semantics**:
  - `terminal_any` fires on `completed | completed_with_errors | failed | aborted`.
  - `terminal_fail_only` fires on `completed_with_errors | failed | aborted`.
- **D3 — One delivery row per dispatch**. Email retry state lives in
  `email_delivery` (owned by `EmailService`); webhook retries stay internal
  to the channel. Either way, the `notification_delivery` row records the
  final outcome plus the attempt count.
- **Profile guard**: the notification feature is hidden on the desktop
  profile (`auth_mode=none`) since there is no user identity. All API routes
  return 403 in that mode. To be revisited alongside D1.

## Non-goals

- Per-run opt-out ("mute this run"). Not modelled — delete the subscription
  or change the trigger.
- SMS / push / in-app bell notifications.
- Digest mode (hourly / daily summaries).
- Webhook payload templating by subscription. A single Slack-compatible
  envelope ships for all webhook subscribers.

## Architecture

### Module layout

```
backend/app/
├── models/
│   ├── notification_subscription.py   # user-owned subscription rows
│   └── notification_delivery.py       # one row per dispatch
├── schemas/
│   └── notification_subscription.py   # Pydantic v2 schemas
├── api/
│   └── notification_subscriptions.py  # CRUD + /{id}/test
├── services/notifications/
│   ├── __init__.py                    # singleton + fire_terminal_notifications()
│   ├── dispatcher.py                  # select → fan-out → record
│   └── channels/
│       ├── base.py                    # ChannelResult, NotificationChannel Protocol
│       ├── email.py                   # delegates to EmailService
│       └── webhook.py                 # owns its own retry loop
```

### Data model

`notification_subscription` — primary row:

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `user_id` | uuid | FK → `user.id`, cascade |
| `plan_id` | uuid? | FK → `load_plan.id`, SET NULL; `NULL` = all plans |
| `channel` | enum | `email` \| `webhook` |
| `destination` | text | email address or `https://` URL |
| `trigger` | enum | `terminal_any` \| `terminal_fail_only` |
| `created_at`, `updated_at` | timestamptz | |

Unique constraint on `(user_id, plan_id, channel, destination)`. Note that
SQL treats `NULL` as distinct in UNIQUE indexes, so duplicates on the
"all plans" row are accepted by the DB and only caught client-side. MVP
accepts this.

`notification_delivery` — one row per dispatch (audit log):

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `subscription_id` | uuid | FK → `notification_subscription.id` |
| `run_id` | uuid? | FK → `load_run.id`, SET NULL; `NULL` for test-sends |
| `is_test` | bool | `TRUE` for rows created by `/test` |
| `status` | enum | `pending` \| `sent` \| `failed` |
| `attempt_count` | int | attempts made by the channel |
| `last_error` | text? | sanitised error message on failure |
| `email_delivery_id` | uuid? | link to `email_delivery` for email sends |
| `sent_at` | timestamptz? | set when `status=sent` |
| `created_at` | timestamptz | |

### Dispatcher flow

```
orchestrator terminal exit
    └─ fire_terminal_notifications(run_id, status)   # fire-and-forget
         └─ NotificationDispatcher.dispatch_run(...)
              1. SELECT matching subscriptions
                 (user_id OR plan_id wildcard matching, trigger-compatible)
              2. for each subscription:
                   dispatch_one(sub, run, is_test=False)
                     a. INSERT notification_delivery row (status=pending)
                     b. channel.send(sub, context)          # channel-specific
                     c. UPDATE row with final status + attempts + error
```

`fire_terminal_notifications` keeps a process-local `set[str]` of run_ids
it has already fired for, so the multiple terminal-exit paths inside the
orchestrator (normal completion, step-threshold abort, cancellation walk,
backstop in `finally`) can all call it safely without double-firing. The
set is capped at 2048 entries; it wraps when full. This is per-process
deliberately — in a multi-worker deploy, run_id uniqueness across workers
is already guaranteed by the DB, so the only real duplicate risk is
*within* a single process's cancellation walk.

### Channel Protocol

```python
class NotificationChannel(Protocol):
    name: str

    async def send(
        self,
        subscription: NotificationSubscription,
        context: Mapping[str, Any],
    ) -> ChannelResult: ...
```

`ChannelResult` carries `accepted: bool`, `attempts: int`, an optional
`error_detail: str`, and — for email — an `email_delivery_id` linking the
row in the email delivery log.

### Retry policy

| Channel | Retries on | Max attempts | Backoff |
|---|---|---|---|
| Email  | handled by `EmailService` per its own classification | 3 | `EmailService` policy |
| Webhook | 5xx, 429, network errors | 3 | `1s * 2^idx + jitter(0..1s)` |

Webhook 4xx is terminal. The channel writes a single `notification_delivery`
row at the end; individual HTTP attempts are **not** rows.

### Webhook payload

```json
{
  "text": "<plan-name>: <run-status>",
  "run": {
    "run_id": "…",
    "load_plan_id": "…",
    "plan_name": "…",
    "status": "completed|completed_with_errors|failed|aborted",
    "started_at": "…",
    "finished_at": "…"
  }
}
```

The `text` field satisfies Slack's simple incoming-webhook contract; the
`run` object lets generic endpoints parse the fuller shape.

## Sanitisation rules

All telemetry goes through `app.observability.sanitization`:

- `sanitize_webhook_url(url)` — strips `?query` and `user:pass@` before any
  URL is logged or placed on a span.
- `redact_email_address(addr)` — local-part truncated before logging.
- Raw destinations (email or URL) are **never** used as metric labels.
- Exception messages go through `safe_exc_message()` to drop anything that
  looks like a secret or URL before entering a log line or `last_error`.

## Observability

Canonical event names (in `app.observability.events.NotificationEvent`):

- `notification.dispatch.requested`
- `notification.dispatch.succeeded`
- `notification.dispatch.failed`
- `notification.webhook.retried`
- `notification.no_matching_subscriptions`

Metrics (in `app.observability.metrics`):

- `sfbl_notification_dispatch_total{channel, status}` (counter, 6 series)
- `sfbl_notification_dispatch_duration_seconds{channel}` (histogram)
- `sfbl_notification_webhook_retry_total{reason}` (counter, 3 series)

See `docs/observability.md § Notification events` for field details.

## REST surface

All routes are gated by `get_current_user` and return 403 when
`auth_mode=none`.

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/notification-subscriptions` | List the caller's own rows |
| `POST` | `/api/notification-subscriptions` | Create; 422 on unknown plan_id, 409 on duplicate |
| `GET` | `/api/notification-subscriptions/{id}` | 403 if owned by another user |
| `PUT` | `/api/notification-subscriptions/{id}` | Re-runs destination validator on the resulting channel |
| `DELETE` | `/api/notification-subscriptions/{id}` | 204 |
| `POST` | `/api/notification-subscriptions/{id}/test` | Synthetic dispatch via the real `NotificationDispatcher`; writes a delivery row with `is_test=TRUE`, `run_id=NULL` |

## Frontend surface

- `pages/settings/NotificationsTab.tsx` — table + add/edit/delete/test,
  under the Settings tabbed shell.
- `pages/settings/SubscriptionFormModal.tsx` — shared add/edit modal.
- `components/NotifyMeButton.tsx` — split-button in the plan-editor toolbar.
  Primary action is a one-click email subscribe for the current user; menu
  offers Customize / Edit / Unsubscribe as state dictates.

Both the tab and the toolbar button are hidden when `auth_mode=none`.

## Open questions / follow-ups

- Plan-ownership ACL (lift D1) — depends on RBAC epic.
- Digest mode / per-step notifications — out of scope.
- Webhook signing (HMAC of payload) — not required by MVP receivers;
  revisit if customers request it.
