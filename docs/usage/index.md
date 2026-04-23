---
title: Using the Bulk Loader
slug: usage-index
nav_order: 0
tags: [index]
summary: >-
  Task-oriented guide to the Bulk Loader UI. Follow in order for first-time setup
  or jump to the topic you need.
---

# Using the Bulk Loader

## What this covers / who should read this

Task-oriented documentation for the people using the Bulk Loader day-to-day —
data-loaders, admins, and operators. Each topic is short, self-contained, and
can be reached via deep link. If you are setting up for the first time, read
the topics under **Getting started** in order; after that you can jump to
whichever feature you need.

These pages are also the source of the in-app `/help` screen (Phase 2,
[SFBL-209](https://matthew-jenkin.atlassian.net/browse/SFBL-209)).

---

## Getting started

| Order | Topic |
|---|---|
| 10 | [Getting started](getting-started.md) — first run, distribution profiles, the bootstrap admin |
| 20 | [Setting up a Salesforce connection](salesforce-connection.md) — Connected App + JWT |
| 30 | [CSV format](csv-format.md) — encoding, headers, relationship notation |
| 40 | [Authoring load plans](load-plans.md) — steps, operations, partition size, error threshold |
| 50 | [Running a load](running-loads.md) — trigger, monitor, abort, retry |

## Data flow & results

| Order | Topic |
|---|---|
| 60 | [The Files pane](files-pane.md) — inputs, outputs, previews, downloads |
| 70 | [Bulk queries](bulk-query.md) — SOQL, validation, chaining into DML |
| 80 | [Output sinks](output-sinks.md) — local vs S3 |

## Staying informed

| Order | Topic |
|---|---|
| 90 | [Notifications](notifications.md) — email + webhook on run completion |

## Admin

| Order | Topic |
|---|---|
| 100 | [User management](user-management.md) — invitations, profiles, lifecycle |
| 110 | [Settings reference](settings.md) — email, Salesforce, security, partitioning |

## Account

| Order | Topic |
|---|---|
| 120 | [Account recovery](account-recovery.md) — forgotten password, locked out |

---

## Conventions used across these pages

- Every page begins with *"What this covers / who should read this"* so you can
  tell at a glance whether you are in the right place.
- Page-level YAML frontmatter declares the topic's title, stable slug,
  navigation order, and (where applicable) the permission key required to see
  it. This is what the in-app `/help` route consumes.
- Each page ends with **Related** cross-links — follow those rather than
  scrolling back to this index.
