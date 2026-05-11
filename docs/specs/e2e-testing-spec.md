# Agent-Authored End-to-End UI Testing

**Status:** Draft spec. Iterate here before opening the epic in Jira.

---

## Background

The codebase has a healthy unit-test culture (`pytest` for the backend,
Vitest + Testing Library for the frontend), but **zero automated
end-to-end testing** at the browser level. As the product has grown —
multi-user RBAC, bulk query, S3 output sinks, named step outputs, AWS
hosting, and now field mapping (SFBL-301) — the surface area no unit
test reaches has grown faster than the unit suite. The cost is paid in:

- UI regressions caught only when an operator stumbles into them.
- Whole-stack integration bugs (frontend ↔ backend ↔ Salesforce)
  passing CI green and breaking in real loads.
- Refactor friction: nobody confidently refactors the orchestrator
  knowing it still drives the UI correctly.

This spec scopes out the enabler that closes that gap: a Playwright-based
E2E suite, gated on every PR for the org-free and metadata-reading
flows, plus a periodic / scheduled tier covering the real Salesforce
integration via scratch orgs.

The naming "agent-authored" reflects how tests come into being: Claude
(or a developer) writes deterministic Playwright specs from feature
descriptions and the existing `docs/usage/*.md` topics. Specs run
deterministically in CI — no LLM in the hot path.

---

## Goals

1. **Close the UI regression gap.** Every shippable user-facing flow
   in `docs/usage/` has at least one E2E spec gating its happy path.
2. **Cover the Salesforce integration honestly.** The Bulk API 2.0
   integration — the highest-risk part of the product — is exercised
   against a real scratch org, not a mock. The H1 finding in SFBL-301
   (relationship-header syntax) is the kind of bug a stubbed Bulk API
   could ship; we won't ship that class of bug again.
3. **Keep CI cost sane.** PR-time CI must stay under ~10 min total.
   Salesforce-touching specs run on a scheduled cadence (nightly +
   release-tag gate + same-repo manual opt-in), not on every PR. See
   D8 for the full trigger policy.
4. **Make new tests cheap to author.** Helper functions, fixtures, and
   page-object patterns mean a new spec is < 50 lines.
5. **Catch fixture drift loudly.** A weekly refresh job re-captures
   describe fixtures against a fresh scratch org and opens a PR if
   anything has changed. Drift between fixture and reality becomes a
   reviewable diff, not a silent rot.

## Non-goals

- **LLM-in-the-loop test runs.** Playwright MCP and similar tools
  belong in the developer's hands for triage / repro, not in the CI
  suite.
- **Visual regression testing.** Screenshot diffing (Chromatic,
  Percy) is a separate concern — useful, complementary, but out of
  scope here.
- **Load / performance testing.** This suite gates correctness, not
  throughput.
- **Stress-testing the Bulk API.** Tier 2 specs use small fixtures
  (~50–500 rows) that exercise behaviour, not scale.
- **Replacing existing unit tests.** Unit tests stay where they are;
  E2E adds a new layer above them.

---

## Locked design decisions

> All decisions in this section are **Locked** as of this draft.
> Revisions require a follow-up note in the changelog.

### D1 — Test framework: Playwright via `@playwright/test` CLI (Locked)

- **Playwright** for browser automation. Multi-browser, parallel-by-
  default, the most agent-friendly API (role-/label-based selectors
  map cleanly to ARIA reasoning). Cypress was the credible alternative;
  ruled out for weaker parallelism, no Webkit, and a less LLM-friendly
  selector model.
- **`@playwright/test` CLI**, not Playwright MCP. Specs are TypeScript
  files in the repo, run deterministically in CI, fail loudly when the
  diff breaks them. The "agent" value lives at *write* time
  (Claude-authored specs), not run time.
- **Playwright MCP remains a developer affordance** for interactive
  triage, bug repro, and "does this still work?" demos. Not part of
  the suite, not a CI gate.

### D2 — Three-tier model (Locked)

The suite splits by what each flow needs from Salesforce, not by
whether it touches Salesforce at all:

| Tier | Covers | Salesforce | Speed | When |
|------|--------|------------|-------|------|
| **1a** | Org-free flows: file pane, plan list, RBAC nav, settings, login | None | ~2 min | Every PR |
| **1b** | Metadata-reading flows: mapping panel, step editor object combobox, SOQL validator, bulk-query builder | Captured describe fixtures | ~3 min | Every PR |
| **2** | State-mutating flows: actual loads, query result writes, retry semantics, byte-exact relationship-header assertions | Real scratch org | ~8–12 min | Nightly cron + release-tag gate + same-repo manual opt-in (see D8) |

Why the split is by-flow-need rather than by-runs-against-real-org:
the field-mapping panel is a metadata-reading flow that genuinely
*requires* a real schema to be meaningful, but doesn't need to mutate
state. Forcing it to Tier 2 would push a quarter of the UI suite into
the slow lane.

### D3 — Salesforce strategy: scratch orgs (Locked)

- Tier 2 specs run against **fresh scratch orgs** spun up at workflow
  start, deployed with the test SFDX project, and torn down at
  workflow end (in an `always()` step so failures don't leak orgs).
- **One scratch org per workflow run**, not per spec. Specs share the
  org and clean their own state between runs (delete records of test
  SObjects in `afterEach`). Trade complexity (cleanup discipline) for
  speed (1× org-spin instead of N×).
- **Per-test-run prefix for record isolation.** Every record a spec
  creates is tagged with a unique prefix:

  ```
  E2E-${RUN_ID}-${WORKER}-${RETRY}-${TEST_SLUG}-
  ```

  **Hyphen-separated, not underscore.** Underscore is a SOQL `LIKE`
  single-character wildcard, so an underscore-built prefix would
  produce false matches in cleanup queries. Hyphens are SOQL-safe.
  The trailing hyphen disambiguates `E2E-...-foo-` from
  `E2E-...-foo-bar-` at prefix-match time.

  Components:
    - `RUN_ID` = `github.run_id` (a uuid generated locally for dev).
    - `WORKER` = `testInfo.workerIndex` (Playwright's per-worker
      index, available on the `TestInfo` object passed to every
      test — **not** `process.env.TEST_WORKER_INDEX`).
    - `RETRY` = `testInfo.retry` (Playwright's per-retry counter,
      0-indexed — **not** `process.env.TEST_RETRY_COUNT`).
    - `TEST_SLUG` = a sanitized slug of the test title (kebab-case,
      alpha/digit/hyphen only — no underscores).

  The prefix is exported by a Playwright fixture as `e2ePrefix`;
  CSV generators consume it as `--prefix`; cleanup runs SOQL with
  **exact prefix** filters (`LIKE '${e2ePrefix}%'`), never the broad
  `LIKE 'E2E-%'`. Without this, parallel files or retried specs
  would count or delete each other's records inside the shared
  scratch org. The `sfQuery` and `wipe_test_records.py` helpers
  assert the prefix is hyphen-only before issuing any `LIKE` query
  to refuse rogue prefixes that happen to slip an underscore in.
- **Default to serial Tier 2 in v1** (`fullyParallel: false` for the
  Tier 2 project in `playwright.config.ts`). The per-test-run prefix
  above is correctness-required even serially (because of retries),
  but disabling parallelism removes one whole bug class from v1
  while the suite is small. Re-enable parallelism if/when wall-clock
  pressure makes it worthwhile and the prefix discipline has had time
  to bed in.
- **Dev Hub** is required. JWT-authed from CI via a private key in GH
  secrets. Local dev requires the developer's own Dev Hub (documented
  in `docs/development.md`).
- **Org Shape configurable** (optional opt-in). Two scratch-def files
  ship with the SFDX project:
    - `config/project-scratch-def.json` (default) — Developer
      edition, no Org Shape, fast spin-up. What nightly + release-tag
      CI uses.
    - `config/project-scratch-def.shaped.json` (opt-in) — references
      a `sourceOrg` ID via Salesforce
      [Org Shape](https://developer.salesforce.com/docs/atlas.en-us.sfdx_dev.meta/sfdx_dev/sfdx_dev_shape.htm),
      so the resulting scratch org mirrors the configuration
      (features, settings, licenses) of a nominated source org without
      committing all its metadata.
    - Selection via env var `E2E_ORG_SHAPE` (`default` |
      `<sourceOrgId>`); the scratch-create helper picks the matching
      `-f` argument. CI never uses Org Shape by default; it's an opt-in
      for local repro of production-shaped issues or manual workflow
      dispatch.
    - **15-character org ID required (locked).** Salesforce CLI's
      `sourceOrg` field accepts a **15-character** org ID. An
      18-character ID (the case-safe form Salesforce returns by
      default in most contexts) is silently mis-handled by some CLI
      paths. The scratch-create helper:
        1. Accepts `E2E_ORG_SHAPE` in either 15- or 18-char form.
        2. **Normalizes to 15 chars** by truncating if input is 18
           chars long.
        3. **Rejects** anything that isn't 15 or 18 chars (clear
           error: "expected 15- or 18-char Salesforce org ID").
        4. Writes the normalized 15-char value into the
           `sourceOrg` field of the in-memory scratch-def JSON
           before invoking `sf org create scratch`.
      Documented for operators in SFBL-330's `development.md`
      section.
    - **Active-shape prerequisite (locked).** `sourceOrg` only works
      when an active org shape exists for the nominated source org.
      Before scratch creation, the helper runs `sf org list shape
      --json` (no `--target-org` flag — that's not part of the
      command's surface) and filters the returned shapes for an
      entry where `sourceOrg` (or its 18-char form) equals
      `E2E_ORG_SHAPE_NORMALIZED` AND `status == "Active"`. If no
      such entry exists, the helper exits with a clear error
      pointing at the manual one-time `sf org create shape
      --target-org <source-org-alias>` operator step (Org Shape
      creation is async — typically ~30 min — so we don't
      block-on-create in the workflow). SFBL-330 documents the
      operator workflow.
- **Why not full sandbox / shared persistent org?** Reproducibility.
  A scratch org is a known starting state. A persistent org accumulates
  test debris and races between concurrent CI runs.
- **Why not stub the Bulk API entirely?** The Bulk API 2.0 *is* the
  product. Stubbing it would let exactly the bugs we want to catch
  (H1 relationship syntax, polymorphic resolution, the Id-only delete
  rule) ship to production. The whole point of Tier 2 is to exercise
  the integration that's hardest to fake.
- **Single Dev Hub for now.** The project has one Dev Hub, used by CI
  and (optionally) by individual developers via their own delegated
  user. If a contribution community grows and external contributors
  need to run Tier 2 locally, they'd need their own Dev Hub — out of
  scope for v1; deferred. Documented in `development.md` so future
  contributors know the constraint up front.

### D4 — Test data: Faker (Python) + tiny generator scripts (Locked)

- **Python `faker`** for CSV input generation. ~6 MB MIT dependency,
  pure stdlib otherwise. The shared `csv_factory.py` (Salesforce-
  shaped, app-blind) lives under `tests/e2e/sf/fixtures/csv/` per
  D13; per-scenario app-specific generator scripts live under
  `tests/e2e/app/fixtures/csv/`.
- **Snowfakery considered and ruled out for v1.** Genuinely nicer for
  4+ object hierarchies with formulas — that's its sweet spot — but
  for our likely shape (flat CSVs, occasional 2-level parent-child),
  the YAML-DSL tax exceeds the readability win. Imperative Python
  scripts are shorter, debuggable in `pdb`, and one fewer tool in the
  chain.
- **Escape hatch.** The input contract is just CSV. If a fixture
  outgrows imperative scripting, dropping in a Snowfakery recipe for
  *that one fixture* is a one-line change in the test setup. We can
  mix.
- **Out of scope:** factory-boy, Mimesis, `@faker-js/faker`. None
  meaningfully better than `faker` for our shape.

### D5 — Salesforce-metadata fixtures: capture-and-replay (Locked)

- Tier 1b avoids touching Salesforce by serving **captured metadata
  responses** to the backend. Fixtures live as JSON under
  `tests/e2e/{sf,app}/fixtures/describe/`, version-controlled.
- Two endpoints are fixture-mode-aware:
    - `/api/connections/{id}/objects` — list of object API names
      (`backend/app/api/connections.py:174-193`). Today hits
      Salesforce's `/sobjects/` listing. Fixture mode reads
      `_object_list.json`.
    - `/api/connections/{id}/objects/{sobject}/describe` — per-SObject
      describe (SFBL-306). Fixture mode reads `{sobject}.json`.

  **Both endpoints must be fixture-aware.** Tier 1b's canary
  (SFBL-322) exercises the object-name combobox, which calls the
  *list* endpoint — without `_object_list.json` support, the canary
  would either hit live Salesforce (defeating Tier 1b) or render an
  empty options list.

- A new env var `SF_DESCRIBE_FIXTURES_DIR=...` controls fixture
  mode. It's **PATH-like**: a colon-separated list of directories,
  searched left-to-right. First match wins. When unset, normal
  behaviour (live Salesforce). The mode is transparent to the
  frontend — same response shape, just different source.

- **Ordered lookup contract.** Standard CI invocation is:

  ```
  SF_DESCRIBE_FIXTURES_DIR="tests/e2e/app/fixtures/describe:tests/e2e/sf/fixtures/describe"
  ```

  Read as: look in the app overlay first, fall back to the
  Salesforce-shaped baseline. This lets an adjacent app (different
  web product, same `sf/` baseline) override describes without
  forking the shared layer.

- **Overlay semantics: complete-replacement for describes,
  list-union for `_object_list.json`.** First-match-wins on a
  describe means an `Account.json` in `app/` **replaces** the
  baseline `Account.json` wholesale — it does not merge. So:
    - When an app needs custom fields on a baseline SObject (e.g.
      `Account.External_Id__c`), it ships a **complete**
      `Account.json` under `app/fixtures/describe/` that includes
      both the baseline fields AND the custom additions. The
      app-specific fixture is captured from a scratch org where
      both the SF baseline and the app's SFDX metadata are
      deployed, so the resulting describe is the union by
      construction. One source of truth, no merge logic in the
      loader.
    - For `_object_list.json` specifically — which is just a list
      of names — the contract is **list-union**: entries from
      every dir on `SF_DESCRIBE_FIXTURES_DIR` are merged (set
      union, sorted alphabetically). Lists are tractable to union
      cleanly; describes aren't.
    - The loader rejects ambiguous overlays at backend startup: if
      two dirs both contain `Account.json` and the contents
      differ in a way that suggests merge intent (e.g. one is
      smaller than the other), it logs a warning. (First-match
      still wins; the warning is to catch operator surprise.)

- This shape mocks at the **right boundary**. The backend's
  describe + object-list endpoints are the architecture's seams to
  Salesforce; replacing their data source preserves the rest of the
  stack (FastAPI handlers, cache, schema, RBAC). Compare to
  Playwright `page.route()` interception, which would short-circuit
  the backend entirely.

- Fixtures cover: `_object_list.json`, `Account.json`,
  `Contact.json`, `User.json`, `Lead.json`, `Opportunity.json`,
  plus our test-only custom objects/fields (in the `app/` overlay).
  Adding a new SObject to the suite means adding one fixture file
  (under `sf/` if generally Salesforce-shaped, `app/` if
  app-specific) and listing it in `_object_list.json` if it should
  appear in the combobox.

- A **fixture refresh** workflow (D9) runs weekly, re-captures all
  fixtures against a fresh scratch org, and opens a PR if anything
  has changed.

**Fixture mode is startup-only and mutually exclusive with live mode
in a given backend process.** `SF_DESCRIBE_FIXTURES_DIR` is read
**once at process startup** and the resolved mode (`fixture` /
`live`) is fixed for the process lifetime. The backend exposes the
mode under the existing `/api/health` endpoint (NOT `/health` —
this app's health route lives under the `/api/` prefix; see
`backend/app/api/utility.py`) for diagnostics and refuses to flip
at runtime.
Implications:

- A backend booted in fixture mode never hits Salesforce for
  describes; the LRU cache (per `field-mapping-spec.md` D2) is
  populated from disk, never from `/services/data/.../describe`.
- A backend booted in live mode never reads fixtures, even if the
  env var is somehow set later (it isn't re-read).
- Tier 1a, 1b, and Tier 2 are run by **separate backend processes**
  in CI — Tier 1b boots with the env var set, Tier 1a/2 boot without.
  No process is ever asked to serve both modes.

This eliminates the cache-poisoning bug class entirely (live cached
describes can't satisfy fixture tests; fixture-loaded entries can't
leak into live-mode tests) without requiring fixture mode/dir/version
to be threaded through the cache key. Cache-key complexity stays
limited to `(connection_id, sobject)` per the field-mapping spec.

The only caveat is local-dev cycle pain: switching between Tier 1b
(fixtures) and Tier 2 (live) requires a backend restart. Acceptable;
documented in `development.md` (SFBL-330).

### D6 — Dev Hub auth in CI: JWT (Locked)

- A connected app on the Dev Hub with a server-key certificate.
- Private key as a GitHub secret (`SFDX_DEVHUB_JWT_KEY`).
- Workflow auth: `sf org login jwt -i $CONSUMER_KEY --jwt-key-file
  /tmp/key.pem --username $DEVHUB_USERNAME --set-default-dev-hub`.
- Local dev: developer logs into their own Dev Hub via `sf org
  login web` (one-time, persists in `~/.sfdx/`).
- Same JWT primitive the bulk loader itself uses for production
  Salesforce auth — operator already understands the shape.

### D7 — Top-level layout pointer (Locked)

> The detailed directory layout for the suite — including the
> Salesforce-shaped vs. app-shaped subtree split — is defined in
> **D13**. This decision (D7) only fixes the top-level *home* and
> the cross-language rationale; the internal structure is owned by
> D13.

- The suite lives at top-level **`tests/e2e/`**, not `frontend/e2e/`.
  It spans Python (CSV generators, capture scripts) and TypeScript
  (specs). A single top-level home avoids the awkwardness of
  "frontend tests that need Python" and aligns with the cross-app
  separation in D13.
- A single **`tests/e2e/package.json`** owns Playwright + lockfile
  for the whole subtree (per D13's "Local developer experience"
  notes) — not a root `package.json`, not under `frontend/`.
- See D13 for the `sf/` (Salesforce-shaped, app-blind) vs. `app/`
  (bulk-loader-specific) subtree split and the import-direction
  lint rule.

### D8 — CI scheduling (Locked)

**Tier 1a + 1b** — every PR (`on: pull_request`). Bring up the local
stack (Docker Compose), run Playwright. Target wall-clock: under 5
minutes combined. Frontend changes get caught at PR time.

**Tier 2** — *not* gated on individual PRs. Three triggers:

- **Nightly cron** (e.g. 02:00 UTC) — catches drift in dependencies,
  Salesforce APIs, and the working day's merges. Failures open a
  GitHub issue (or notify Slack — TBD by ops preference) so the next
  morning's first read is the regression.
- **Release-tag gate, inside `release.yml`.** Tier 2 must actually
  block release-artifact publishing — a separate `on: push tags: v*`
  workflow would run independently and could not stop the existing
  release jobs from attaching artifacts. So Tier 2 lives **inside**
  the existing [`.github/workflows/release.yml`](../../.github/workflows/release.yml)
  as an upstream job; the artifact-publishing jobs gain a
  `needs: [tier-2-e2e]` dependency. The tag pattern matches
  `release.yml`'s existing filter (`v[0-9]+.[0-9]+`) — **not** the
  permissive `v*` an earlier draft proposed; mismatched filters would
  let some tagged releases skip the gate entirely.
- **Manual label opt-in** (`e2e-tier-2` on a PR), restricted to
  same-repo PRs only — secrets policy spelled out below.

**Secrets policy for Tier 2 (locked).** Tier 2 needs the Dev Hub JWT
private key, the test-only bulk-loader JWT private key, and the
scratch-org alias. Repository secrets must never be exposed to PR
code from forks.

- The label-opt-in workflow uses `on: pull_request` (**not**
  `pull_request_target`). `pull_request_target` would run with the
  base branch's secrets accessible to the PR's code — the standard
  "running PR code with prod secrets" footgun GitHub explicitly warns
  about.
- The label-opt-in job carries `if: github.event.pull_request.head.repo.fork == false`,
  so fork PRs do not enter the job at all (and would not receive
  secrets even if they did, under `pull_request`).
- Fork PRs needing Tier 2 verification require a maintainer to run a
  `workflow_dispatch` against a trusted ref (typically `main` after
  reviewing the diff, or a maintainer-curated branch).
- These constraints documented at the top of the workflow file with
  a comment block linking to GitHub's
  [pull_request_target security guidance](https://securitylab.github.com/research/github-actions-preventing-pwn-requests/).

**No automatic per-PR path trigger.** "Salesforce-touching" is an
ill-defined predicate (a refactor in `partition_executor.py` could
trip a path filter; a one-line typo fix in `salesforce_bulk.py`
shouldn't). Reviewers start trusting a CI signal that may or may not
have run, which is worse than a predictable nightly. Keep the surface
predictable: Tier 1a/1b at PR time; Tier 2 at boundary moments
(nightly, release, same-repo opt-in).

Tier 2 wall-clock target: under 15 minutes including scratch-org
spin-up. Since Tier 2 doesn't run on every PR, latency is not a
critical-path constraint — scratch-org pooling is therefore *not*
worth its complexity for v1.

**Implication: regression window.** A Tier 2-breaking change merged
just after the nightly run isn't caught for ~24h. The mitigations:
(a) reviewers label risky PRs to force a Tier 2 run pre-merge;
(b) Tier 1a/1b cover the *frontend* side of any regression and run
on every PR; (c) the release-tag gate ensures nothing actually ships
unverified. Acceptable trade for the CI cost saved.

### D9 — Fixture-refresh workflow (Locked)

- Runs `on: schedule` weekly (Sunday 02:00 UTC) and on `workflow_dispatch`.
- Spins up a temporary scratch org with the test SFDX project,
  including any custom SObjects/fields the suite depends on.
- Runs `tests/e2e/sf/scripts/capture_describes.py`, which writes
  JSON files into `tests/e2e/sf/fixtures/describe/` (baseline) and,
  for app-specific custom-field augmentations,
  `tests/e2e/app/fixtures/describe/`.
- If `git diff` in that directory is non-empty, opens a PR titled
  `chore(e2e): refresh describe fixtures (week of YYYY-MM-DD)` with a
  per-SObject diff summary in the body.
- Tears down the scratch org regardless of outcome.

This catches Salesforce-side drift loudly: an admin renames a custom
field, a Salesforce release adds a new flag to describe payloads, our
fixture goes stale — all surface as a reviewable PR with the change
spelled out, rather than as a Tier 2 spec failure 6 weeks later.

### D10 — App-state fixturing: API POSTs, not DB-level (Locked)

- Specs that need pre-existing app state (a `Connection`, a
  `LoadPlan`, a `LoadStep`) create that state by **calling the
  application's own API** in spec setup, not by writing rows directly
  via SQLAlchemy or seeding the SQLite/Postgres database.
- Why: exercises the API surface the way users do, including
  validation and RBAC. A DB-level fixture can paint over a broken
  POST endpoint and let the bug ship. API setup also stays
  schema-agnostic if we ever migrate the data model again.
- Helpers in `tests/e2e/app/playwright/helpers/api.ts` wrap the
  common setup operations: `createConnection()`, `createPlan()`,
  `createStep()`, etc. Specs are short and declarative. These
  helpers live under `app/` (not `sf/`) per D13 — they call the
  bulk loader's own API, so they're app-specific.

### D11 — Test-only auth for the bulk loader against the scratch org (Locked)

The bulk loader itself uses JWT auth (currently against a Salesforce
Connected App) to load data. Tier 2 specs need the bulk loader to
authenticate to the *scratch org*, not production Salesforce, so the
test harness must provide a JWT-capable client app inside each
scratch org.

**Constraint that drives this decision.** As of Salesforce Spring '26,
new Connected App creation is being progressively blocked across UI,
Tooling API, and Metadata API in favour of **External Client Apps
(ECAs)**. The original draft of this spec assumed we could deploy a
fresh `ConnectedApp` metadata file into each scratch org per run; that
path is no longer reliably buildable. Two viable paths remain:

#### Primary: External Client App (ECA) deployed via SFDX

ECAs are Salesforce's forward path for OAuth-capable apps and support
the JWT bearer flow.

> **Implementation-readiness gate: SFBL-332 spike.** The exact ECA
> metadata shape, scratch-def feature flags, and consumer-key
> discovery flow are non-trivial and not implementation-ready from
> documentation alone. SFBL-332 is a prerequisite spike that proves
> the setup against a real scratch org once and documents the working
> shape. SFBL-324 (metadata) and SFBL-327 (auth wiring) are blocked
> on the spike. The shape sketched below is a starting point — the
> spike report supersedes it.

**Sketch of what the test harness ships** (subject to spike
revision):

- **Scratch-def features.** `config/project-scratch-def.json` must
  enable the ECA feature set. Likely needs
  `"features": [... "ExternalClientApp" ...]` and an
  `"externalClientAppSettings": { "enableExternalClientApps": true }`
  settings block. Spike confirms the exact shape.
- **Per-ECA metadata** (the app itself):
    - `force-app/main/default/externalClientApps/SfblE2E.eca-meta.xml`
      — ECA definition.
    - `force-app/main/default/extlClntAppOauthSettings/SfblE2E.eca.crt-meta.xml`
      — per-ECA OAuth settings including cert reference.
- **Global ECA metadata** (org-wide setup the spike will have
  uncovered):
    - `force-app/main/default/extlClntAppGlobalOauthSets/<name>.ecaGlblOauth-meta.xml`
      — global OAuth settings. **Carries the generated consumer
      key/secret** post-deploy/retrieve, per Salesforce's ECA Metadata
      API docs.
    - `ExtlClntAppSecretExposeCtl` configuration controlling whether
      the secret is retrievable after deploy.
- **Certificate**: a public certificate file generated from a
  test-only key pair, referenced by the OAuth settings file. Public
  cert is committed (it's a public key); the private key counterpart
  is the GitHub secret `SFBL_E2E_BULK_LOADER_JWT_KEY`. Local dev
  keeps a copy in `~/.sfbl-e2e/` (documented in SFBL-330, never
  committed).
- **Permission set** granting the required ECA OAuth policy +
  assigning the test System Administrator user.

**Flow per Tier 2 workflow run** (subject to spike revision):

1. `sf project deploy start --target-org "$E2E_SCRATCH_ORG"` —
   deploys ECA + global OAuth settings + cert + permission set.
2. `sf org assign permset --target-org "$E2E_SCRATCH_ORG" --name SfblE2E`
   — assigns the permission set to the scratch admin user.
3. **Consumer-key discovery.** The consumer key is generated by
   Salesforce post-deploy and lives in the **global OAuth settings**
   file (NOT in a `ClientId__c` field on `ExternalClientApp` — an
   earlier draft of this spec named the wrong source). The spike
   confirms the working discovery mechanism. Two candidate methods:
    - **Retrieve + parse**: `sf project retrieve start` pulls the
      deployed global OAuth file back; the helper greps the
      consumer key from the retrieved XML.
    - **REST API**: `sf api request rest`
      `/services/data/<api>/connect/external-client-apps/<name>` (if
      such an endpoint exists in current API versions; spike
      confirms).
4. **JWT smoke test** — a fixed first step in the Tier 2 workflow,
   *before any spec runs*: authenticate as the bulk loader against
   the scratch org via JWT, fetch a token, and exit. If this fails,
   the workflow exits with a clear ECA-setup error rather than
   failing downstream specs with confusing symptoms.
5. Tier 2 spec setup: configure a bulk-loader `Connection` row
   pointing at the scratch org, with the PEM-contents of the private
   key loaded from the GH secret and the consumer key from step 3.
   `Connection.private_key` is stored as a PEM **string**, not a
   path (per `backend/app/schemas/connection.py`).

#### Fallback: pre-packaged Connected App in a 1GP/2GP package

If ECA setup proves harder than the timeline permits — JWT-on-ECA is
relatively new and may have edge cases — a fallback path is to:

1. Create a Connected App **once**, manually, in a Dev-Hub-adjacent
   org we control.
2. Package it as a 1GP managed or 2GP unlocked package.
3. Install the package into each scratch org in step 1 of the Tier 2
   workflow (`sf package install --target-org "$E2E_SCRATCH_ORG"
   --package <id>`).

This sidesteps the Spring '26 creation block (the Connected App
already exists; we install it, we don't create it). Trade: an
external dependency on the package source org.

**Choice locked: ECA primary; fallback documented but not
implemented unless ECA blocks.** SFBL-324 starts with ECA. If a real
blocker surfaces (e.g. JWT-on-ECA flow fails in scratch orgs for our
edition), the fallback is the documented escape hatch and SFBL-324 pivots
without a re-spec.

> **Wider implication beyond this spec.** The same Spring '26
> constraint affects the *production* setup path documented in
> `docs/usage/salesforce-jwt-setup.md` and `salesforce-connection.md`
> — anyone setting up the bulk loader for the first time post-Spring '26
> needs ECA, not Connected App. **Out of scope for this enabler**, but
> flagging here so it doesn't get lost. Worth a follow-up Jira issue
> against the field-mapping epic's parent ecosystem; existing customers
> are unaffected.

### D12 — Post-load assertion: `sf data query` (Locked)

Tier 2 specs verify Salesforce state after a load via `sf data query`
from within the Playwright spec, shelled out from a helper.

**Helper contract (locked).** Every shell-out — assertion, capture,
cleanup, fixture refresh — uses the same flag set:

```bash
sf data query \
  --target-org "$E2E_SCRATCH_ORG" \
  --json \
  --query "<SOQL>"
```

`--target-org "$E2E_SCRATCH_ORG"` is **mandatory**, never inferred
from `sf` config. D6 sets the *Dev Hub* as default, not the scratch
org; relying on the default would silently query the wrong org.
`--json` is **mandatory** so the helper parses structured output, not
human-formatted tables.

The helper signature reflects this:

```ts
async function sfQuery(
  soql: string,
  opts: { targetOrg?: string } = {},
): Promise<Record<string, unknown>[]> {
  const target = opts.targetOrg ?? process.env.E2E_SCRATCH_ORG;
  if (!target) throw new Error("E2E_SCRATCH_ORG not set");
  // exec sf data query --target-org "$target" --json --query "$soql"
  // parse .result.records
}
```

Example usage in a spec, with the D3 per-test-run prefix (hyphen-
only, never underscore):

```ts
test("Account insert end-to-end", async ({ page, e2ePrefix }) => {
  // e2ePrefix is supplied by the Playwright fixture from
  // testInfo.workerIndex + testInfo.retry + a sanitized slug,
  // all hyphen-separated. Never contains "_" or "%".
  const rows = await sfQuery(
    `SELECT Id, Name, External_Id__c FROM Account WHERE External_Id__c LIKE '${e2ePrefix}%'`
  );
  expect(rows.length).toBe(50);
});
```

`sf` CLI is already on the runner for org lifecycle, so reusing it is
free. `jsforce` was considered (in-process; no shelling out) but adds
a dependency for marginal gain.

### D13 — Cross-app structural separation: `sf/` vs. `app/` (Locked)

The intended class of consumers is broader than this app: web /
integration apps that straddle Salesforce — pushing to or pulling
from an org while exercising a non-Salesforce frontend. Anything in
this enabler that's *Salesforce-shaped* should be reusable across
that class without carrying bulk-loader assumptions; anything
*bulk-loader-shaped* must stay isolated so it doesn't leak into the
shared layer.

This is a **structural** separation only — not a framework, not a
package, not a versioned API. Just file boundaries with a one-way
import direction enforced by lint.

**Layout:**

```
tests/e2e/
├── sf/                              # Salesforce-shaped, app-blind
│   ├── playwright/
│   │   └── helpers/                 # sfQuery, scratch-org auth, fixture loader
│   ├── sfdx/                        # SFDX project: ECA, cert, baseline permset
│   ├── scripts/                     # capture_describes, scratch lifecycle
│   ├── fixtures/csv/                # csv_factory + ExternalId/parent helpers
│   └── package.json (subset re-exported by tests/e2e/package.json)
├── app/                             # Bulk-loader-specific
│   ├── playwright/
│   │   ├── tier-1a/                 # Specs
│   │   ├── tier-1b/
│   │   ├── tier-2/
│   │   └── helpers/                 # Page objects, app API setup, app fixtures
│   ├── sfdx/                        # App-specific custom objects/fields/permsets
│   ├── scripts/                     # App-specific helpers (none initially)
│   └── fixtures/                    # App-specific scenario CSVs and describes
└── package.json + playwright.config.ts  # Top-level entry; references both subtrees
```

**One-way import direction (locked):** `app/` may import from `sf/`,
but `sf/` **never** imports from `app/`. Enforced by an ESLint
boundary rule (`eslint-plugin-boundaries` or
`@typescript-eslint/no-restricted-imports`) failing CI on violation.
A failing lint in CI is the boundary's only enforcement; humans
review the rest.

**What this gets us:**

- The `sf/` subtree is a pre-extracted candidate. If a second
  consumer materialises (a sibling Salesforce-targeting app on the
  same team), extracting `sf/` to a shared repo is moving a directory
  and lifting the lint rule, not a refactor week.
- For a single consumer today, the layout is still cleaner — readers
  know whether a given helper is Salesforce-generic or app-specific
  without grepping.
- The `sf/sfdx/` and `app/sfdx/` split prevents an app-specific
  custom field (e.g. SFBL-301's test SObject) ending up in the
  shared SFDX baseline.

**What this is not:**

- Not a published package (no version, no registry, no `npm publish`).
- Not a public API (no backward-compat promise; either subtree may
  break the other within this repo until extraction).
- Not a framework (no abstract base classes designed for unknown
  consumers; just helpers with sensible signatures).
- Not enforced for fixture *content* — `sf/fixtures/csv/` carries
  Faker-based generators useful to any Salesforce app; app-specific
  scenario CSVs still go under `app/fixtures/`.

**Extraction trigger.** If/when a second consumer is concrete (not
speculative), the extraction is: move `tests/e2e/sf/` to a separate
repo, replace it with a git submodule or dependency, lift the
import-direction lint rule. Estimated 1–2 weeks of focused work at
that point, dominated by configuration-externalisation and writing
the shared layer's own meta-tests.

The enabler ships as **three bundled wave PRs**, not one. Unlike
SFBL-301 — which is a coherent feature where merging half leaves
the product inconsistent — this enabler has natural seams:

- Wave 1 stands alone (Tier 1a working = real value).
- Wave 2 stacks on top (Tier 1b adds metadata coverage).
- Wave 3 is the highest-cost addition (scratch-org infrastructure +
  Tier 2 coverage).

Each wave is shippable; later waves can defer if priorities shift.

### Sequencing vs. SFBL-301 (field mapping)

SFBL-301's UI lands with E2E coverage from day one, not retrofitted.
Concretely:

- **Enabler waves 1 + 2 must merge before SFBL-311..315 starts.**
  The mapping panel ships with Tier 1b spec coverage as part of its
  acceptance criteria, not as a follow-up.
- **SFBL-301 waves 1 + 2 (backend) proceed in parallel** with this
  enabler — they don't need E2E to ship.
- **Enabler wave 3 (Tier 2) can land in parallel with SFBL-301
  wave 3.** Tier 2 is the actual-Salesforce gate, useful but not
  blocking the panel work itself.

This means SFBL-311..315 each pick up Tier 1b spec coverage in their
acceptance criteria once enabler waves 1–2 are merged. SFBL-313's
byte-exact relationship-header assertions (the H1 lock-in) get
covered in *both* tiers: Tier 1b confirms the UI produces the right
string given fixture metadata; Tier 2 confirms Salesforce actually
accepts the string in a job submission.

### Wave 1 — Foundation + Tier 1a

- **SFBL-317** — Playwright scaffold: install `@playwright/test`,
  `playwright.config.ts`, npm scripts, lint integration. Add the full
  `tests/e2e/` layout per D7/D13 (including `sf/{playwright,sfdx,
  scripts,fixtures/{csv,describe}}` and `app/{playwright,sfdx,
  fixtures/{csv,describe}}` skeleton directories so subsequent
  tickets can drop files in). CI helper to bring up the local stack
  via Docker Compose.
- **SFBL-318** — Tier 1a canary spec: one spec covering the file-pane
  flow — **seed** a small CSV into the local input directory via a
  `beforeEach` filesystem write (no upload UI exists; the Files page
  is browse/preview only), log in as fixture user, navigate to
  `/files`, assert the seeded file appears, open the preview pane,
  teardown deletes the seeded file. No Salesforce. Proves the loop.
- **SFBL-319** — CI workflow part 1: Tier 1a runs on `pull_request`.
  Fail-fast on a real spec failure, surface Playwright HTML report
  as a workflow artefact.

### Wave 2 — Tier 1b enablement + SFBL-301 pickup gate (metadata-reading specs)

- **SFBL-320** — Backend: `SF_DESCRIBE_FIXTURES_DIR` env var support
  (per D5). PATH-like; both the `/objects` list endpoint and the
  per-SObject describe endpoint are fixture-aware. Tests cover
  fixture and live modes.
- **SFBL-321** — `tests/e2e/sf/scripts/capture_describes.py` —
  authenticates against a scratch org via `sf` CLI, calls
  `/services/data/.../sobjects/` (list) and
  `/services/data/.../sobjects/{name}/describe` (per-SObject)
  using REST (not SOQL — see SFBL-321 description), writes trimmed
  JSON to `tests/e2e/sf/fixtures/describe/` (with `_object_list.json`
  for the list endpoint). Initial fixture set committed
  (Account, Contact, User, Lead, Opportunity, plus `_object_list.json`).
- **SFBL-322** — Tier 1b canary spec: the **step editor's**
  object-name combobox (`StepEditorModal` in the plan editor) shows
  the captured options and the per-SObject describe populates
  downstream UI. Validates the fixture loop end-to-end (frontend →
  backend → fixture file → response).
- **SFBL-331** — DoD update + retroactive reconciliation of
  SFBL-311..315's speculative Tier 1b sections against the merged
  harness. **This is the SFBL-301 pickup gate.** Moved into Wave 2
  because the gate must clear before SFBL-301 wave 3 can start,
  which is the whole point of investing up-front (Q5). Adds a
  parallel rule to `CLAUDE.md`'s "Epic Definition of Done —
  documentation" section.

> **Wave 2 ships static committed fixtures only.** The recurring
> fixture-refresh workflow (SFBL-323) needs the Tier 2 SFDX project
> (SFBL-324) and the ECA setup (SFBL-332) to spin up a real scratch
> org for re-capture — so it lives in Wave 3, not here. Wave 2's
> fixtures come from SFBL-321's one-time capture commit; staleness
> is acceptable until Wave 3 brings the refresh online.

### Wave 3 — Tier 2 enablement (scratch-org integration)

- **SFBL-332 (SPIKE)** — Prove ECA + JWT-bearer setup against a real
  scratch org once. Produces the full metadata shape, scratch-def
  feature list, a reliable consumer-key discovery flow, and a smoke
  JWT auth. **Blocks SFBL-324 and SFBL-327** — both pick up the
  spike's artefacts. Doesn't need to be merged; the spike report is
  the deliverable.
- **SFBL-324** — SFDX project skeleton (per D7): `sfdx-project.json`,
  `config/project-scratch-def.json` (incl. the
  `externalClientAppSettings.enableExternalClientApps` feature
  flag), custom test SObjects/fields, permission sets, and the
  test-only **External Client App** metadata per D11 (ECA + global
  OAuth settings + cert reference). **Gated on SFBL-332 spike.**
- **SFBL-325** — Dev Hub JWT auth in CI (D6): GitHub secrets,
  `sf org login jwt` workflow step, scratch-org create/destroy helper
  scripts (incl. Org Shape 15-char ID normalization per D3).
  Always-run cleanup so failures don't leak orgs.
- **SFBL-326** — `csv_factory.py` (D4): Faker-backed helpers for
  ExternalId minting, parent-row picking, deterministic-seed
  generation. Hyphen-only prefix per D3. Three initial scenario
  scripts (accounts insert, contacts upsert with Account lookup,
  opportunity update).
- **SFBL-327** — Test-only bulk-loader JWT auth wiring (D11):
  helper that loads the test-only private key (as PEM string) +
  sets up a bulk-loader `Connection` row pointing at the scratch
  org. Includes a `wipe_test_records.py` cleanup helper for
  `afterEach` hooks. **Gated on SFBL-332 spike.**
- **SFBL-328** — Tier 2 canary spec: end-to-end Account insert.
  Generate 50 Accounts via `csv_factory`, **seed the generated CSV
  into the local input directory** (no upload UI exists — same
  filesystem-seed pattern as SFBL-318), author the bulk-loader plan
  against the seeded file pattern via UI, run the plan, assert via
  `sf data query` that 50 records exist with the expected
  ExternalIds. Also covers the H1 byte-exact relationship-header
  assertion via a Contact upsert with `Account__r.External_Id__c`,
  same filesystem-seed pattern.
- **SFBL-323** — Fixture-refresh CI workflow (D9). Weekly cron;
  spins up a scratch org with the Wave-3 SFDX project (per
  SFBL-324) + ECA (per SFBL-332), runs `capture_describes.py`,
  opens a PR with the refreshed fixtures and a diff summary. **Moved
  to Wave 3** (review-pass H1) because it depends on Wave-3 SFDX
  + ECA infrastructure; the prior placement in Wave 2 was
  internally inconsistent.
- **SFBL-329** — Tier 2 CI scheduling per D8. Three triggers wired:
  (a) nightly cron in a dedicated workflow file;
  (b) release-tag gate **inside** `.github/workflows/release.yml` as
  an upstream job — `release.yml`'s artifact jobs gain a
  `needs: [tier-2-e2e]` dependency, and the tag filter aligns to the
  existing `v[0-9]+.[0-9]+`;
  (c) same-repo `e2e-tier-2` PR label opt-in via `on: pull_request`
  with explicit `types: [labeled, synchronize, reopened]` (never
  `pull_request_target`) and `if: ...head.repo.fork == false`.
  Plus `workflow_dispatch` for maintainer-driven manual runs against
  a trusted ref. Workflow-file comment block links to the GitHub
  Actions secrets-security guidance per D8.

### Wave 4 — Documentation (small)

- **SFBL-330** — `docs/development.md`: new "End-to-end testing"
  section covering local Dev Hub setup, the single-Dev-Hub
  constraint for v1 (with a note for future external contributors),
  running specs locally, authoring conventions (page-object pattern,
  fixture helpers, naming), the Org Shape opt-in (incl. 15-char
  source-org ID normalization per D3), and the tier model. Brief —
  most readers want a working command, not a textbook.

> **What used to be SFBL-331 lives in Wave 2 now** as the SFBL-301
> pickup gate. Wave 4 is therefore docs-only.

---

## Architecture notes

### Spec authoring conventions

- **Page-object pattern.** One module per page in
  `tests/e2e/app/playwright/helpers/pages/` (under `app/` per D13 —
  page objects map to the bulk loader's own UI). Specs read like
  prose: `await mappingPanel.openFieldDestinationPicker(rowIndex)`,
  not raw `page.locator('[data-testid=...]')` calls. Keeps specs
  resilient to UI markup changes.
- **`getByRole` / `getByLabel` over `data-testid` where possible.**
  Forces the UI to be accessible; the test selectors and the screen-
  reader experience converge. `data-testid` is the fallback when
  ARIA can't disambiguate.
- **One spec per user-visible flow**, not per component. A flow is
  the atomic unit of "did this still work?" — e.g. "Author and run
  an Account insert plan" is one flow, not five.
- **No conditional skips for Tier 2 in Tier 1 runs.** Each tier's
  specs live in their own directory; the CI job picks the directory.
  Avoids the failure mode where a spec silently runs in the wrong
  tier.

### Local developer experience

**Playwright lives at `tests/e2e/package.json` with its own
lockfile.** Not at the repo root (no root `package.json` exists in
the project today, and adding one for E2E alone would muddy the
frontend's tooling), and not under `frontend/` (that would couple the
E2E layer to the React app's lifecycle and confuse CI cache paths).
A self-contained `tests/e2e/` package keeps the suite's deps,
lockfile, and lint config independent and aligns with the cross-app
structural boundary in D13.

Commands run from `tests/e2e/`:

- `npm run e2e:1a` — runs Tier 1a against a locally-running stack.
- `npm run e2e:1b` — runs Tier 1b (requires fixture files; no SF needed).
- `npm run e2e:2` — runs Tier 2 against a scratch org (requires Dev
  Hub auth and `E2E_SCRATCH_ORG` set).
- `npm run e2e:scratch:create` / `npm run e2e:scratch:destroy` —
  scratch-org lifecycle helpers wrapping `sf org create scratch` /
  `sf org delete scratch` with the test SFDX project.

CI cache key includes `tests/e2e/package-lock.json`; cwd for the
Playwright steps is `tests/e2e/`. SFBL-317 locks these in `package.json`
+ workflow scaffolding.

### Cleanup discipline

Tier 2 specs share a scratch org per workflow. Every spec must:

1. Tag created records with the per-test-run prefix (D3):
   `E2E-${RUN_ID}-${WORKER}-${RETRY}-${TEST_SLUG}-` (hyphen-only,
   never underscore — underscore is a SOQL `LIKE` wildcard). The
   Playwright `e2ePrefix` fixture provides the value; CSV generators
   consume it as `--prefix`.
2. Clean up its own records in `afterEach` via
   `wipe_test_records.py` with an **exact** prefix filter
   (`LIKE '${e2ePrefix}%'`). Never the broad `LIKE 'E2E-%'` — that
   could delete records belonging to a parallel worker or a retry.
   The helper rejects any prefix containing `_` or `%` before
   issuing the query (defence-in-depth).

A nightly **scratch-org-leak detector** (separate small workflow)
queries the scratch org for E2E records older than the workflow's
expected lifetime and surfaces an alert if any persist. Cheap insurance
against cleanup bugs.

---

## Resolved decisions log

The first Q&A pass on the draft (2026-05-10) worked through five
questions; outcomes folded back into the locked decisions above.
Recap for traceability:

| # | Question | Resolution | Folded into |
|---|----------|-----------|-------------|
| Q1 | Is per-PR Tier 2 the right scheduling? | **No.** Drop the path-trigger entirely. Tier 2 runs only on nightly cron + release tags + manual `e2e-tier-2` PR label. Predictable surface > "may or may not have run." Acceptable trade: ~24h regression window covered by Tier 1a/1b at PR time and the release-tag gate. | D8 |
| Q2 | What is scratch-org pooling? | **Explained + ruled out for v1.** CumulusCI pre-creates a stash of empty orgs, served in ~10 s vs. ~3 min for fresh creation. With Tier 2 only on nightly + tags (Q1), spin-up time isn't critical-path; pooling's complexity exceeds its benefit. | D8 (mention) |
| Q3 | Single Dev Hub vs. multiple? | **Single Dev Hub for v1.** Contribution-community case (external contributors needing their own Dev Hub for Tier 2 local runs) deferred. Documented as a known constraint in `development.md`. | D3, SFBL-330 |
| Q4 | Org Shape configurability? | **Yes**, as opt-in. Two scratch-def files ship; `E2E_ORG_SHAPE` env var picks between default Developer edition and an opt-in shaped org referencing a `sourceOrg` ID. CI defaults to non-shaped; opt-in for local repro / manual dispatch. | D3 |
| Q5 | Invest up front or retrofit E2E onto SFBL-301? | **Up front.** Enabler waves 1 + 2 (Tier 1a + 1b) must merge before SFBL-311..315 starts. The mapping panel ships with Tier 1b spec coverage as part of its acceptance criteria, not a follow-up. SFBL-331 becomes material — it edits SFBL-311..315 in Jira to reflect the new bar. | Story-breakdown sequencing section, SFBL-331 |

## Remaining open questions

1. **Bulk API limit pinch.** Scratch orgs have lower Bulk API limits
   than full sandboxes. A nightly Tier 2 run with 10 specs and
   ~500 rows/spec is well within limits, but worth measuring in week
   one. Mitigation: scenario scripts default to 50 rows; the
   row-count is a fixture parameter, not hardcoded.
2. **Salesforce edition for the default scratch org.** Default
   (Developer) lacks some features (e.g. some platform events,
   advanced sharing). Fine for our purposes today, but worth flagging
   — if we ever test a feature that needs a different edition, the
   default scratch-def.json is the place. Org Shape (Q4) is the
   escape hatch when the difference is configuration rather than
   edition.
3. **Test data privacy.** Faker generates synthetic data, but a
   developer running specs locally against a personal Dev Hub could
   theoretically log identifying data into a scratch org. Specs must
   never import real-world CSVs; this is a documentation-and-review
   concern, not a technical one.
4. **Nightly failure notification surface.** Tier 2 nightly failures
   should be visible the next morning. Options: open a GitHub issue
   automatically (simple, version-controlled), Slack notification
   (requires the existing Slack webhook plumbing — SFBL-117), or
   both. Resolve at workflow-build time (SFBL-329).

---

## Risks

- **Flake.** Browser-driven E2E is the flakiest tier of testing.
  Mitigations: Playwright's auto-waiting (no `sleep` calls), strict
  selectors (avoid generic `text=Save`), per-spec isolation in
  Tier 2. A flaky spec that re-runs green is a bug to fix, not a
  retry to add.
- **CI cost creep.** Each new spec adds wall-clock. Wave-1 budget
  (5 min combined Tier 1a/1b) is comfortable; revisit if it nears
  10 min. Parallelism via Playwright's `workers` config is the
  first lever; Tier 1a/1b split across two jobs is the second.
- **Scratch-org capacity.** The Dev Hub has a daily scratch-org
  creation cap (100/day on most plans). Nightly + on-`main` +
  occasional PRs keeps us well under, but a weekend with many merges
  could pinch. Worth monitoring; the fix is pooling (above).
- **Salesforce API change blast radius.** A Salesforce release that
  changes describe payload shape would break Tier 1b until the
  fixture-refresh PR lands. The refresh workflow runs weekly so the
  window is bounded, and Salesforce's API changes are usually
  additive. Acceptable.
- **ECA / JWT setup drift.** Salesforce Spring '26 forces ECA over
  Connected App for new creation (D11). JWT-on-ECA is relatively new
  and may have edge cases on Developer-edition scratch orgs.
  Mitigation: the Tier 2 workflow's first step is a JWT smoke test
  (D11) that authenticates as the bulk loader against the scratch
  org and exits — a clear ECA-setup failure beats opaque downstream
  spec failures. Fallback path (pre-packaged Connected App in a 1GP
  / 2GP package) is documented in D11; SFBL-324 pivots to it without a
  re-spec if ECA blocks.

- **SFBL-301 sequencing is a critical-path dependency, not just
  coordination.** Per the "Sequencing vs. SFBL-301" section, the
  SFBL-301 pickup gate is **SFBL-317..322 + SFBL-331** merged
  (Wave 1 + Wave 2). SFBL-323 (recurring fixture refresh) lives in
  Wave 3 and is **NOT** a pickup blocker — Wave 2 ships static
  committed fixtures from SFBL-321's one-time capture, which is
  enough to author and run Tier 1b specs.
  SFBL-311..315 carry speculative Tier 1b acceptance criteria
  (added before this enabler existed); those are placeholders
  against an unbuilt harness, so implementing them before SFBL-331
  reconciles them would mean writing specs against helper APIs
  that may not match merged reality. Concrete handling:
    - Implementation order is enforced at ticket-pickup time.
      Anyone starting an SFBL-31x story confirms SFBL-317..322 and
      SFBL-331 have all merged first, or pauses.
    - **SFBL-331 is the reconciliation gate.** When enabler waves 1+2
      merge, SFBL-331's acceptance criteria include sweeping
      SFBL-311..315 to align their Tier 1b sections with the
      now-real harness API (replacing speculative helper names,
      fixture mechanics, selector conventions with their merged
      equivalents). Without SFBL-331 first, SFBL-31x implementers
      would waste effort on stale specs.
    - If timeline pressure means SFBL-301 wave 3 must start before
      enabler waves 1+2 are done, the fallback is to defer the Tier
      1b acceptance criteria on SFBL-31x to a follow-up story —
      explicit retrofit, with a Jira link, rather than silent
      compromise. This is the only condition under which Q5's
      "invest up front" stance is relaxed.

---

## Related

- [SFBL-301 field mapping spec](field-mapping-spec.md) — the first
  feature whose UI wave should ship with E2E coverage from day one
  rather than retrofitting.
- [`docs/architecture.md`](../architecture.md) — the system the suite
  is testing.
- [`docs/usage/`](../usage/) — task-oriented topics that each Tier 1a
  / 1b spec mirrors.
- [`docs/development.md`](../development.md) — gets a new section
  via SFBL-330.
- Playwright docs — https://playwright.dev/

---

## Changelog

- **2026-05-10** — Initial draft. Twelve locked decisions D1–D12.
  Story breakdown across four waves (15 stories total). Ready for
  Q&A pass before ticket creation.
- **2026-05-10** — Q&A pass. Five questions resolved and folded
  back into the locked decisions:
    - **Q1** scheduling: dropped per-PR path-trigger for Tier 2.
      Tier 2 now runs nightly + release tags + manual label opt-in
      only. Updated D8 with rationale and the regression-window
      mitigations.
    - **Q2** scratch-org pooling: explained and ruled out for v1
      given Q1's direction makes pooling's complexity unjustified.
      Recorded in D8.
    - **Q3** Dev Hub: single Dev Hub for v1; contribution-community
      case deferred. Documented in D3 and SFBL-330.
    - **Q4** Org Shape: opt-in via second scratch-def file +
      `E2E_ORG_SHAPE` env var. Folded into D3.
    - **Q5** sequencing: up-front investment. Enabler waves 1+2
      gate SFBL-311..315; SFBL-331 becomes material (edits SFBL-301
      tickets to add Tier 1b coverage as acceptance criteria).
      Added "Sequencing vs. SFBL-301" section to story breakdown.
- **2026-05-10** — SFBL-311..315 edited in Jira to add a *Tier 1b
  spec coverage (speculative)* section to each. The sections are
  flagged speculative — they were authored from this draft before
  the enabler is implemented, so helper APIs, fixture mechanics, and
  selector conventions are likely to drift. Each section instructs
  the implementer to re-read the latest enabler spec and helper
  modules on pickup and update or replace the bullets accordingly.
  **Reconciliation checkpoint:** when enabler waves 1–2 merge, the
  SFBL-331 work item should sweep SFBL-311..315 and bring their Tier
  1b sections into line with the merged harness reality. Track this
  as part of SFBL-331's acceptance criteria once that ticket is
  created.
- **2026-05-10** — Technical-review fix pass. Ten findings
  (3 High, 5 Medium, 2 Low) plus the cross-app structural
  separation deferred from earlier:
    - **H1 ECA over Connected App.** Spring '26 blocks new
      Connected App creation. D11 fully rewritten: External Client
      App as primary path (with consumer-key discovery via Tooling
      API, profile/permset assignment, JWT smoke test as the Tier 2
      workflow's first step); pre-packaged Connected App documented
      as fallback. Wider implication for the production setup path
      flagged as out-of-scope-but-noteworthy.
    - **H2 Release-tag gate must live inside `release.yml`.** A
      separate workflow can't block release-artifact publishing.
      D8 + SFBL-329 rewritten: Tier 2 lives inside `release.yml` as
      an upstream job; artifact-publishing jobs gain
      `needs: [tier-2-e2e]`; tag pattern aligned to the existing
      `v[0-9]+.[0-9]+`.
    - **H3 Secrets policy spelled out.** Use `pull_request` (not
      `pull_request_target`); restrict label opt-in to same-repo
      PRs via `if: ...head.repo.fork == false`; fork PRs require
      maintainer `workflow_dispatch` against a trusted ref.
      Workflow file carries a comment block linking to GitHub's
      pull_request_target security guidance.
    - **M1 Path-trigger contradiction purged.** Goals, tier table,
      and SFBL-329 cleaned up. Resolved-decisions log retains the
      historical context.
    - **M2 Cache poisoning prevented.** D5 makes fixture mode
      startup-only and process-isolated:
      `SF_DESCRIBE_FIXTURES_DIR` is read once at backend boot, mode
      is fixed for the process lifetime, exposed under `/health`,
      refuses runtime flips. Tier 1b and Tier 2 always run against
      separate backend processes. No cache-key change required.
    - **M3 Per-test-run cleanup prefix.** D3 + cleanup-discipline
      section now require
      `E2E_${RUN_ID}_${WORKER}_${RETRY}_${TEST_SLUG}_` from a
      Playwright fixture; cleanup uses exact-prefix `LIKE`, never
      the broad `LIKE 'E2E_%'`. Default Tier 2 to serial
      (`fullyParallel: false`) for v1 simplicity.
    - **M4 `sf data query` helper contract locked.**
      `--target-org "$E2E_SCRATCH_ORG" --json` mandatory across
      assertion, capture, cleanup. Helper signature documented in
      D12; relying on `sf` config defaults rejected.
    - **M5 SFBL-301 sequencing promoted to project risk.** Risks
      section calls out the critical-path dependency explicitly,
      points to SFBL-331 as the reconciliation gate, and documents
      the only condition under which Q5's "invest up front" stance
      relaxes (deferred retrofit with explicit Jira link).
    - **L1 Org Shape active-shape prerequisite.** D3 documents the
      `sf org create shape` / `sf org list shape` operator step
      and adds an availability check to the scratch-create helper.
    - **L2 Playwright at `tests/e2e/package.json`.** Locked: own
      lockfile, cwd for CI steps, cache-key path; not at repo
      root, not under `frontend/`. Aligns with D13.
    - **NEW D13 Cross-app structural separation.** New locked
      decision: `tests/e2e/sf/` (Salesforce-shaped, app-blind) vs.
      `tests/e2e/app/` (bulk-loader-specific) with one-way import
      direction enforced by ESLint boundary rule. D7 reduced to a
      pointer; detailed layout owned by D13. Pre-extracts the
      shared layer for the class of consumers the user named
      (mixed Salesforce + non-Salesforce ecosystem apps) without
      adopting any framework tax (no package, no version, no
      public API).
- **2026-05-11** — Tickets created. Main epic SFBL-316; child
  stories SFBL-317..331 across four waves. Story-breakdown section
  updated with real keys (placeholders `EE-N1..N15` swapped for
  `SFBL-317..331` 1:1).
- **2026-05-11** — Second-pass technical review fix pass. Ten
  findings (3 High, 5 Medium, 2 Low):
    - **H1 ECA setup not implementation-ready.** D11's metadata list
      was incomplete (missing `extlClntAppGlobalOauthSets`,
      `ExtlClntAppSecretExposeCtl`, the scratch-def
      `externalClientAppSettings.enableExternalClientApps` feature)
      and the consumer-key discovery method was wrong
      (`SELECT ClientId__c FROM ExternalClientApp` is not how
      Salesforce surfaces the generated key). **Added SFBL-332 as a
      prerequisite spike** to prove the setup against a real scratch
      org and document the working shape; SFBL-324 / SFBL-327 are
      blocked on the spike. D11 updated with a sketch + spike
      reference; the spike report supersedes the sketch.
    - **H2 Tier 1b didn't cover object-list endpoint.** D5 only
      fixtured per-SObject describes, but the connection wizard's
      combobox calls `/api/connections/{id}/objects` (the SObject
      list endpoint). D5 expanded: both endpoints are fixture-aware
      (`_object_list.json` for the list, `{sobject}.json` for
      describes); `SF_DESCRIBE_FIXTURES_DIR` is now PATH-like with
      ordered lookup (app overlay → sf baseline); SFBL-320 grows
      to cover the list endpoint, SFBL-321 captures
      `_object_list.json`, SFBL-322 canary validates both.
    - **H3 File-pane canary depended on nonexistent upload UI.**
      `FilesPage.tsx` is browse/preview only; there is no upload UI
      or API. SFBL-318 rewritten to seed a CSV into the local input
      directory before the test, then browse + preview it.
    - **M1 SFBL-327 violated `sf/` vs `app/` boundary.**
      `setup_connection.ts` belongs in `app/` (calls the bulk
      loader's own API). Also corrected: `Connection.private_key`
      schema takes a PEM string, not a path. Ticket updated.
    - **M2 Label opt-in workflow missing activity types.**
      `on: pull_request` defaults to `[opened, synchronize,
      reopened]` — not `labeled`. SFBL-329 updated to specify
      `pull_request: types: [labeled, synchronize, reopened]`.
    - **M3 `pull_request_target` misused as a per-step thing.** It's
      a workflow trigger. SFBL-323 dropped that wording; the
      fixture-refresh workflow stays `schedule + workflow_dispatch`
      with explicit `contents: write` + `pull-requests: write`
      permissions for the PR-creation step.
    - **M4 Prefix isolation correctness traps.** Underscore is a
      SOQL `LIKE` wildcard; D3's prefix had underscores throughout.
      Also `process.env.TEST_RETRY_COUNT` doesn't exist —
      Playwright surfaces retry count on `testInfo.retry`. D3
      rewritten to use hyphen separators and `testInfo.retry` /
      `testInfo.workerIndex`. Helpers assert prefix is hyphen-only
      before issuing any SOQL `LIKE`.
    - **M5 `sf data query` can't issue REST describe calls.**
      `sf data query` is SOQL only. SFBL-321 updated to use
      `sf api request rest` (or `curl` + `sf org display --json` for
      auth) — the spike (SFBL-332) confirms which is available in
      the pinned CLI.
    - **L1 Stale Connected App phrase in story breakdown.**
      Replaced with "External Client App metadata per D11" for
      SFBL-324's bullet.
    - **L2 `EE-N*` references in SFBL-319 / SFBL-322 ticket text.**
      Swapped to `SFBL-319` / `SFBL-322` self-references and
      "this ticket / later Wave 2 ticket" wording.
- **2026-05-11** — Third-pass technical review fix pass. Ten more
  findings:
    - **H1 Critical-path internally inconsistent.** Wave 2's
      SFBL-323 fixture-refresh workflow depended on Wave-3 SFDX +
      ECA infrastructure; SFBL-331 reconciliation gate was in
      Wave 4 despite being the actual SFBL-301 pickup gate. Wave
      restructure: **SFBL-323 moves from Wave 2 to Wave 3**
      (after the SFDX + ECA prerequisites land); **SFBL-331 moves
      from Wave 4 to Wave 2** (it's the gate that unblocks
      SFBL-311..315). Wave 2 ships static committed fixtures
      from SFBL-321's one-time capture; the recurring refresh is
      Wave 3.
    - **M1 SFBL-328 still said \"upload CSV via UI\".** Same
      bug class as SFBL-318 (no upload UI / API exists). Ticket
      rewritten to seed generated CSVs into the local input
      directory before authoring the plan, mirroring SFBL-318's
      filesystem-seed pattern.
    - **M2 Tier 1b canary pointed at the wrong UI surface.**
      SFBL-322 referenced a \"connection wizard object combobox\"
      but the actual object picker lives in the load-plan step
      editor (`StepEditorModal` in `PlanEditor`). Ticket
      retargeted to the plan editor's `StepEditorModal` flow.
    - **M3 Fixture overlay semantics ambiguous.** PATH-like
      first-match-wins cannot extend a baseline describe — it
      replaces it wholesale. D5 now locks the contract:
      **complete-replacement for per-SObject describes**
      (overlays are full files, captured from a scratch org
      where both baseline + app metadata are deployed);
      **list-union for `_object_list.json`** (lists are tractable
      to union cleanly). Loader logs a startup warning on
      ambiguous overlay duplicates.
    - **M4 Stale fixture paths in spec.** D9 still pointed at
      `tests/e2e/scripts/...` and `tests/e2e/fixtures/describe/`;
      SFBL-317's scaffold omitted the describe fixture dirs.
      D9 updated to use `tests/e2e/sf/scripts/...` and
      `tests/e2e/{sf,app}/fixtures/describe/`; SFBL-317 layout
      bullet expanded to include the full subtree (sf/app x
      playwright/sfdx/scripts/fixtures/{csv,describe}).
    - **M5 Prefix examples reintroduced underscore wildcard.**
      D12 example still used `currentTestName` and
      `LIKE '${prefix}_%'`; cleanup section still showed
      `E2E_${...}_`. Both rewritten to hyphen-only with the
      `e2ePrefix` Playwright fixture, matching D3.
    - **M6 Health endpoint path wrong.** Spec said `/health`;
      the app's health route is `/api/health` (per
      `backend/app/api/utility.py`). D5 corrected; SFBL-320
      ticket updated to test against `/api/health`.
    - **M7 SFBL-332 spike printed access token.** Spike asked
      `jwt_smoke_test.sh` to print the issued access token.
      Updated to print a redacted success summary only; never
      log access tokens or consumer secrets. Matches existing
      codebase convention (`backend/app/services/salesforce_auth.py`).
    - **M8 SFBL-330 docs-drift gate doesn't cover its file.**
      The `docs-drift` CI job and `check-help-links.mjs` only
      scan `docs/usage/**`; SFBL-330 edits `docs/development.md`,
      which would not be checked. Ticket DoD revised: drop the
      inapplicable gates; add a manual-review checklist and an
      optional follow-up to extend the lint scope.
    - **L1 SFBL-318 stale in spec story summary.** Spec wave-1
      bullet still said \"upload a tiny CSV\"; aligned to the
      filesystem-seed pattern the ticket actually uses.
    - **L2 Org Shape ID 15-char normalization.** Salesforce CLI
      `sourceOrg` expects a 15-character org ID; an 18-character
      ID silently fails or behaves unpredictably. D3 + SFBL-325
      now require the scratch-create helper to normalize 18→15
      and reject malformed input; SFBL-330 documents the
      operator-facing constraint.
- **2026-05-11** — Fourth-pass review fix pass. Five findings (no
  Highs):
    - **M1 `sf org list shape` flag wrong.** The previous draft
      had the helper run `sf org list shape --target-org <id>`,
      but that flag doesn't exist on the command. Reshape: helper
      now runs `sf org list shape --json` and filters the
      returned shapes in-process by normalized source-org ID +
      `Status: Active`. Also corrected the operator-facing
      `sf org create shape --target-org <source-org-alias>` to
      note it takes an alias, not an org ID. D3, SFBL-325,
      SFBL-330 all updated.
    - **M2 Risk section + epic still described old wave shape.**
      Risk section said the gate was `SFBL-317..N7` and implied
      SFBL-323 was a pickup blocker. Updated to spell out the
      actual gate: **SFBL-317..322 + SFBL-331 merged**, with
      SFBL-323 explicitly noted as Wave 3 and NOT a blocker
      (Wave 2's static committed fixtures are sufficient). Epic
      SFBL-316 description rewritten with the same wave map +
      gate.
    - **M3 Helper paths still violated D13 layout.** D4 had
      `tests/e2e/fixtures/csv/`, D10 had
      `tests/e2e/playwright/helpers/api.ts`, Architecture notes
      had `tests/e2e/playwright/helpers/pages/`. All swept to
      the D13 layout: shared csv_factory under
      `tests/e2e/sf/fixtures/csv/`; per-scenario generators
      under `tests/e2e/app/fixtures/csv/`; API setup helpers
      under `tests/e2e/app/playwright/helpers/api.ts`; page
      objects under `tests/e2e/app/playwright/helpers/pages/`.
    - **M4 SFBL-328 had unresolved SFBL-301 dependency.** The
      upsert canary spec was authored to use \"raw mapping rows
      via API\" — a path that doesn't exist until SFBL-301's
      mapping data + API stories ship. Reshape: SFBL-328 now
      uses a **pass-through CSV** whose header is already in
      destination form (`Account__r.External_Id__c`). The
      orchestrator submits the header unchanged (today's
      pass-through behaviour, locked into SFBL-307); the H1
      byte-exact assertion still locks at the Salesforce
      boundary. SFBL-328 has no SFBL-301 dependency. H1 trap is
      split cleanly: SFBL-313 covers UI persistence (Tier 1b);
      SFBL-328 covers orchestrator → Salesforce (Tier 2).
    - **L1 Stale \"connection wizard\" wording.** Tier table
      and SFBL-322 summary still said \"connection wizard\";
      updated to \"step editor object combobox\" matching the
      retargeted flow.
