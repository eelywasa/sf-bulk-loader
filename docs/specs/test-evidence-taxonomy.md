# Test evidence taxonomy

> SFBL-334 / SFBL-342. The cross-layer contract for how Playwright + pytest
> annotate Allure reports so the dashboard at
> [reports.bulkloader.forcetide.net](https://reports.bulkloader.forcetide.net)
> stays consistent regardless of which test suite produced the result.

## Status

Live. This is the **active** contract every layer's helper module
implements. Changes here ripple to the per-layer helpers
(`tests/e2e/sf/playwright/helpers/allure.ts` and
`backend/tests/_allure_helpers.py`); update them in lockstep.

## What this covers

- **Labels** — key/value pairs displayed in the Allure report's faceted
  filters, used to slice + group test runs.
- **Links** — clickable references (Jira tickets primarily) attached to
  individual test cases.
- **Categories** — the failure-classification taxonomy Allure uses to
  bucket red runs into actionable groups (`infrastructure` vs `flaky`
  vs `real regression`).
- **URL layout** in the evidence bucket — what paths the publish
  workflow (SFBL-345 E) writes to, so internal cross-links and the PR
  comment template can be authored against stable shapes.
- **Conventions** for new test authors — which annotations belong on
  which kinds of test.

## Labels

Every label uses lowercase keys. Values are case-sensitive enums where
noted.

| Key | Type | Valid values | Required? | Purpose |
| --- | --- | --- | --- | --- |
| `tier` | string enum | `1a`, `1b`, `2` | required on Playwright specs | Maps to the Playwright project (`tier-1a` / `tier-1b` / `tier-2`); the same Allure run can contain multiple tiers. |
| `layer` | string enum | `e2e`, `backend` | required on every test | Distinguishes Playwright (`e2e`) from pytest (`backend`) results so the dashboard's "Behaviors" view groups them cleanly. |
| `owner` | string | GitHub handle (e.g. `eelywasa`) | optional, recommended on long-lived suites | Whose pager goes off when this regresses. Surfaces in the test history panel. |
| `feature` | string | free-text noun phrase | optional, recommended on Tier 2 | Lets the dashboard group tests by user-facing feature ("bulk-query", "field-mapping"). |

Implementation: helpers push annotations with `type` equal to the key
above. `allure-playwright` and `allure-pytest` both treat custom
annotation/label types as facets in the report.

## Links

| Type | Required? | Format |
| --- | --- | --- |
| `issue` | required if the test was added in response to a specific Jira ticket | `https://matthew-jenkin.atlassian.net/browse/SFBL-XXX` — full URL; the helper builds it from a bare `SFBL-XXX` key |

Other Allure-recognised link types (`tms`, etc.) are not used today —
revisit if we ever integrate a separate test-management system.

## Categories

Allure renders failures grouped by category. The classification lives
at `<allure-results>/categories.json` and is identical for every layer.

```json
[
  {
    "name": "Infrastructure failures",
    "matchedStatuses": ["broken"],
    "messageRegex": ".*(ECONNREFUSED|getaddrinfo|TimeoutError|HTTPError|RequestException|CloudFront|S3|Throttling).*"
  },
  {
    "name": "Flaky / retry passed",
    "matchedStatuses": ["passed"],
    "flaky": true
  },
  {
    "name": "Real regression",
    "matchedStatuses": ["failed"]
  }
]
```

- **Infrastructure failures**: anything `broken` whose message names a
  transport-layer or AWS-service error. These are typically not the
  test author's bug — they're a noisy edge / dependency / quota issue.
- **Flaky / retry passed**: tests that needed a retry to go green. The
  dashboard should highlight these so we can investigate the
  underlying flake; they don't fail CI but they do degrade signal.
- **Real regression**: a `failed` status. This is the bucket that
  should be empty in steady state. Anything here needs an owner + a
  fix or a justified `@allure.issue` linking out to a follow-up ticket.

The publish workflow (SFBL-345 E) writes the same `categories.json` to
every `allure-results/` directory before running `allure generate`.

## URL layout in the evidence bucket

| Prefix | What lives here | Retention |
| --- | --- | --- |
| `main/` | Latest report from a successful main-branch CI run | indefinite |
| `pr-{n}/` | Per-PR snapshot, overwritten on each push | 30 days (S3 lifecycle) |
| `tier-2/{run-id}/` | Per Tier-2 scheduled run, immutable | 90 days |
| `tier-2/history/` | Shared Allure history (trend, retry tracking) across every Tier-2 run | retained while any Tier-2 run is live |

Authoritative S3 bucket: `bulkloader-testevidence-evidencebucketfba44255-dul0vrjuirrp`
in `us-east-1` (live since SFBL-341).
CloudFront URL: `https://reports.bulkloader.forcetide.net/{prefix}/`.

The Lambda@Edge OAuth gate rewrites any URI ending in `/` to
`{uri}index.html`, so `https://reports.../pr-123/` resolves to
`pr-123/index.html`.

### History-merge model

Allure builds its trend graph by pulling the previous run's `history/`
directory into the staging dir before `allure generate`. Each prefix
needs to pick: *per-prefix history* (the simple default — each `pr-{n}/`
has its own trend across that PR's pushes) or *shared history across runs*
(the trend spans all runs that belong to one logical track).

| Prefix | History model | Why |
| --- | --- | --- |
| `main/` | per-prefix (`main/history/`) | One canonical track — every push to main extends the same trend. |
| `pr-{n}/` | per-prefix (`pr-{n}/history/`) | Trend is scoped to that PR's iteration; merging with main's history would muddy the signal. |
| `tier-2/{run-id}/` | **shared** (`tier-2/history/`) | SFBL-348 decision. Each Tier-2 run gets a unique `{run-id}` so per-run history would orphan every report. The dashboard's value for Tier 2 is precisely the trend across runs ("did this regression land yesterday or has it been broken for a fortnight?"), which requires a single shared history surface. |

Implementation: the publish wrapper (`tests/e2e/scripts/publish-evidence.sh`)
supports an optional `HISTORY_PREFIX` env var. When set, it pulls
history from `s3://${BUCKET}/${HISTORY_PREFIX}/` instead of
`${PREFIX}/history/` and pushes the freshly generated `history/`
subdirectory back to the same path after the main sync. `HISTORY_PREFIX`
is set to `tier-2/history` for Tier-2 publishes only; everything else
inherits the default per-prefix behaviour.

## Conventions for new test authors

### Playwright (e2e)

- Apply `labelTier(testInfo, '<your project's tier>')` and
  `labelLayer(testInfo, 'e2e')` once at the top of the test body.
- If the test was added in response to a Jira ticket, add
  `linkIssue(testInfo, 'SFBL-XXX')` in the same place.
- `owner()` is optional but recommended for long-lived smoke tests on
  Tier 2 — the dashboard's owner facet lets you find "all
  Salesforce-side regressions Matt owns" at a glance.

### pytest (backend)

- Decorate the module or test with
  `@label_tier('1a')` / `@label_layer('backend')` (the `tier` value for
  pytest modules is always `1a` today — backend tests are not yet
  cross-tier).
- Use `@link_issue('SFBL-XXX')` when the test exists to cover a
  specific ticket.
- Apply at module level (covers every test in the file) unless a
  single test has a different ticket / owner.

### What NOT to annotate with

- Don't add a label for the *spec file path* — Allure already has that
  built-in.
- Don't use `feature` as a substitute for `tier`; they're different
  axes (`tier` is "where does this run?", `feature` is "what
  user-facing thing does this exercise?").
- Don't link to PR URLs — link to the originating Jira ticket. The PR
  is one step removed; tickets are the durable identifier.

## See also

- [`tests/e2e/sf/playwright/helpers/allure.ts`](../../tests/e2e/sf/playwright/helpers/allure.ts) — TS helper implementation
- [`backend/tests/_allure_helpers.py`](../../backend/tests/_allure_helpers.py) — Python helper implementation
- [`docs/operations/test-evidence-runbook.md`](../operations/test-evidence-runbook.md) — operator runbook (rotation, access, incident)
- [`docs/architecture/aws-topology.md`](../architecture/aws-topology.md#test-evidence-host-sfbl-334) — host topology
- [SFBL-342](https://matthew-jenkin.atlassian.net/browse/SFBL-342) — this contract's owning story
- [SFBL-334](https://matthew-jenkin.atlassian.net/browse/SFBL-334) — parent epic
