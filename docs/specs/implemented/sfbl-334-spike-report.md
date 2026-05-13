# SFBL-334 Phase 1 spike — Allure + GitHub Pages hosting feasibility

> **Historical record.** This spike was run 2026-05-12 to decide where the
> cross-layer test-evidence dashboard should live. The verdict it
> produced — "GitHub Pages wins" — was **subsequently overturned** in the
> same epic after a Codex review surfaced the public-by-default failure
> mode (a public repo's `gh-pages` branch would make any redactor
> regression a one-way credential leak).
>
> **The live architecture is private S3 + CloudFront + Lambda@Edge GitHub
> OAuth**, documented at
> [`docs/architecture/aws-topology.md`](../../architecture/aws-topology.md#test-evidence-host-sfbl-334).
> Operator procedures live at
> [`docs/operations/test-evidence-runbook.md`](../../operations/test-evidence-runbook.md).
>
> This file preserves the spike's findings (size numbers, history-merge
> verification, framework gotchas) because several of them carried
> forward into the production implementation. Don't take the verdict at
> the bottom as the live decision.

## Hosting spike — verdict at the time: GitHub Pages wins

3 sequential runs of the spike workflow on `spike/sfbl-334-allure-gh-pages`
(run 1 via push, runs 2 + 3 via `workflow_dispatch`) deployed cleanly to
the `gh-pages` branch and merged history across each push.

Deployed report at the time: `https://eelywasa.github.io/sf-bulk-loader/`
(decommissioned alongside Wave 4's gh-pages cleanup, [SFBL-349](https://matthew-jenkin.atlassian.net/browse/SFBL-349)).

## Run-level numbers

Each spike run = 10 tests, 2 with explicit attachments, all with
project-level trace + screenshot capture.

| Metric | Value |
| --- | --- |
| Wall-clock per run | 49s |
| `allure-results/` raw | ~740 KB (33 files) |
| `allure-report/` generated | ~1.6 MB |
| Largest dynamic file | 152 KB (`trace.zip` from the mocked-network test) |
| Constant Allure UI bundle | ~604 KB (one-off; shared across all runs) |
| Total `gh-pages` branch size after 3 runs | ~2.1 MB (uncompressed, includes git history) |

These numbers carried forward into the production S3 + CloudFront
design — the per-report size envelope (~1.6 MB generated) is what
informed the lifecycle policy choices (`pr-*/` 30d, `tier-2/*/` 90d,
`main/` retained) and the size sentinel threshold (~50 MB per report)
codified in Wave 1.

## History continuity verified

`history/history-trend.json` after run 3:

```json
[ { "data": { "passed": 10, "total": 10 } },
  { "data": { "passed": 10, "total": 10 } },
  { "data": { "passed": 10, "total": 10 } } ]
```

Each run's `history/` folder is pulled from the prior deployment,
merged into the new `allure-results/`, then re-emitted inside the
freshly generated report. The pattern works on any storage backend
(gh-pages or S3); the S3 publish workflow (SFBL-345) implements the
same merge.

## Extrapolation to Tier 2

Per-test cost is roughly:

- 1 result.json: ~4–8 KB
- 1 screenshot: ~8–140 KB
- 1 trace.zip: ~12–152 KB

A Tier 2 run with ~30 specs, each carrying a trace + screenshot, would
weigh ~3–5 MB of generated report. Retaining 50 historical runs ≈
150–250 MB. Comfortably under any per-bucket / per-distribution quota
we'd hit at this scale; informed the production lifecycle policy.

## Gotchas worth carrying into Wave 2+

Most of these survived into the live infrastructure or the helper
modules under SFBL-342.

1. **Reporter option is `resultsDir`, not `outputFolder`.** Default if
   omitted is `./allure-results`.
2. **`test.use({ trace, screenshot })` is forbidden inside a
   `describe` block** — Playwright requires those at file or project
   level. Force capture on Tier 2 via the project's `use` block in
   `playwright.config.ts`.
3. **`--reporter=` CLI flag overrides the config-defined reporter
   array entirely.** If anyone passes `--reporter=list` on an
   Allure-instrumented suite the Allure output silently vanishes —
   document in the Wave 2 wiring guide.
4. **`page.setContent()` + `page.route()` only intercepts absolute
   URLs** — the spike `fetch('/api/health')` fixture initially failed
   because relative URLs resolve against `about:blank`. Not relevant
   for real Tier 1/2 specs that use the actual app, but worth noting if
   anyone copies the spike pattern.
5. **GH Pages is public** because the repo is public. *This was the
   load-bearing finding that overturned the verdict.* Any Tier 2 trace
   that lands in `gh-pages` is publicly indexable; one redactor
   regression = one-way leak. The S3 + CloudFront + Lambda@Edge OAuth
   model shipped in Wave 1 ([SFBL-341](https://matthew-jenkin.atlassian.net/browse/SFBL-341))
   moves the access boundary into infrastructure so the redactor
   becomes defense-in-depth rather than the sole gate.
6. **Concurrency**: the spike used `cancel-in-progress: false` to let
   pushes queue serially. Per-PR runs need the same to avoid two
   simultaneous deploys racing on the publish ref. Carried into the
   Wave 2 publish workflow design under [SFBL-345](https://matthew-jenkin.atlassian.net/browse/SFBL-345).

## Spike artefacts

All removed pre-Wave-1:

- Branch `spike/sfbl-334-allure-gh-pages` — deleted local + remote
  2026-05-12 before Wave 1's CDK stack went live. The spike workflow,
  spec dir, and `spike` project in `playwright.config.ts` never landed
  on `main`.
- `gh-pages` branch + deployed site at
  `https://eelywasa.github.io/sf-bulk-loader/` — kept temporarily during
  Wave 1 as a reference. Disabled + deleted in
  [SFBL-349](https://matthew-jenkin.atlassian.net/browse/SFBL-349)
  (Wave 4) once the S3 host fully replaced it.

## Why the verdict was overturned — see also

- [SFBL-334 epic](https://matthew-jenkin.atlassian.net/browse/SFBL-334)
  — third comment carries the Codex review that surfaced the
  public-by-default failure mode and triggered the S3/CF pivot
- [`docs/architecture/aws-topology.md`](../../architecture/aws-topology.md#test-evidence-host-sfbl-334)
  — the live topology that replaced the spike's gh-pages setup
- [`docs/operations/test-evidence-runbook.md`](../../operations/test-evidence-runbook.md)
  — the operator runbook for the live architecture
- [SFBL-341](https://matthew-jenkin.atlassian.net/browse/SFBL-341) +
  [SFBL-350](https://matthew-jenkin.atlassian.net/browse/SFBL-350)
  — the two Wave 1 stories that delivered the live infrastructure
