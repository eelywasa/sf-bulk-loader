# Parallel Agent Execution — Rules and Pitfalls

Guidance for orchestrating multiple Claude Code agents running in parallel across worktrees on this repo.

Adapted from the laser-tank runbook (2026-03-30) and extended with SFBL-specific lessons from the SFBL-136 email-service wave (2026-04-17). Portable rules live here; stack-specific commands target SFBL's Python/FastAPI backend + React/Vite frontend + Jira-backed workflow.

---

## Model selection strategy

Default to the cheapest model that can do the job. Escalate only when evidence says it's needed.

| Tier | When | Examples |
|---|---|---|
| **Haiku** | Doc edits, env-var blocks, template copy writing, simple config changes, Jira state transitions, tiny refactors that don't cross module boundaries | Rewriting `auth/password_reset/body.html` copy; appending to `.env.example`; Jira comment + transition on ticket close; single-file test fixture |
| **Sonnet** (default) | Standard feature implementation — endpoints, migrations, React pages, test suites, orchestration of one ticket's worth of scope | Every SFBL-145 through SFBL-150 child ticket; any backend route + tests; any frontend page + tests |
| **Opus** | Only when Sonnet has demonstrably failed or when the task requires subtle reasoning across several interacting constraints that a Sonnet run has already proven hard | **Not pre-assigned to any SFBL-119 ticket.** Reserve for genuine escalations (e.g. a tricky merge-conflict resolution, a concurrency bug that Sonnet can't diagnose in two tries, a security review where the architecture itself is in question) |

**Orchestration tip:** write the ticket descriptions precisely enough that Sonnet doesn't need to reason architecturally — all the design decisions should already be locked. The SFBL-119 tickets (SFBL-145 to SFBL-151) follow this pattern: every non-enumeration, rate-limit, watermark, and template decision is stated explicitly, leaving the agent with implementation only.

**Never launch the orchestrator itself as Opus for a parallel wave unless the plan itself is in doubt.** Use Sonnet for orchestration too — the runbook below is designed to make the orchestration mechanical.

---

## Rule 1 — Always start a wave on a feature branch

Before launching any agents, create a dedicated integration branch and push it:

```bash
git checkout main && git pull origin main
git checkout -b feat/<wave-or-feature-name>
git push -u origin feat/<wave-or-feature-name>
```

All agent worktrees and all merges target this branch. After the final agent completes, raise a single PR from the feature branch to `main`. **Never merge agent work directly to `main`.**

**Why it matters:** Without a single integration branch there is no PR, no review gate, and no clean revert path. SFBL-136 followed this rule (`feat/email-service` → PR #39) and the revert surface stayed small.

---

## Rule 2 — Push the integration branch after each batch before launching the next

The `isolation: "worktree"` mechanism seeds new worktrees from `origin/<branch>`, not from your local HEAD. If earlier agents have merged to the local integration branch but you haven't pushed yet, the next batch will be based on stale code.

After each batch merges:

```bash
git push origin feat/<wave-name>
# then launch the next batch
```

---

## Rule 3 — Explicitly block scope creep in every agent prompt

Include a hard scope restriction as the **first substantive instruction** in every agent prompt:

```
You are implementing ONLY <SFBL-XXX> (and optionally <SFBL-YYY>).
Do NOT implement any other tickets — not even ones you find in the spec or that look
closely related. If you encounter out-of-scope work, leave a TODO comment and move on.
```

Name sister agents so territorial boundaries are unambiguous:

```
Note: parallel agents are simultaneously implementing <SFBL-AAA> and <SFBL-BBB>.
Do not touch that work.
```

**Why it matters:** In the laser-tank Wave 4, one agent read the full epic spec and silently implemented two downstream tickets. The dedicated agents for those tickets then had nothing to commit, and their Jira state was left inconsistent. The SFBL-143 agent had a similar derailment in SFBL-136 — a tighter scope fence in the prompt would have prevented it.

---

## Rule 4 — Pin the worktree path and validate edits target it

The `Edit` and `Write` tools operate on **absolute file paths** and have no awareness of which worktree is active. An agent editing `/Users/.../sf-bulk-loader/backend/app/foo.py` edits the main repo, not its isolated worktree copy.

Include this block in every worktree-isolated agent prompt (substitute the actual path):

```
Your worktree root is: <WORKTREE_PATH>

All file edits MUST use full paths under your worktree root:
  <WORKTREE_PATH>/backend/app/api/auth_reset.py           ✓
  /Users/.../sf-bulk-loader/backend/app/api/auth_reset.py ✗ (this is the main repo)

Before staging, always run:
  git -C <WORKTREE_PATH> status
and confirm that your changed files appear there, not in the main repo.
```

---

## Rule 5 — Limit each agent's scope to avoid merge conflicts

Agents that touch the same file create merge conflicts. Two strategies:

**a) Functional partitioning** — assign each agent a distinct logical area of the file (e.g. "only the `EmailService.send_template` branch of `service.py`"). Call this out in the prompt so the agent doesn't refactor surrounding code.

**b) Sequential batching** — if two agents must both modify the same file heavily, run them in separate batches (batch 1 merges before batch 2 starts) rather than in parallel.

New files (new modules, new tests, new templates) are safe to author in parallel because they don't conflict.

**SFBL-specific hot files** — these attract conflicts and need special care:
- `backend/app/main.py` — router registration. If two agents add routers in parallel, merge is usually clean but not guaranteed.
- `backend/app/schemas/auth.py` — multiple schemas may be added in one wave. Have each agent append to a named region, or have the last-merged agent rebase.
- `backend/app/api/auth.py` — avoid multiple agents editing. Prefer putting new flows in new modules (e.g. `auth_reset.py`, `me.py`).
- `frontend/src/api/endpoints.ts` — same pattern: append-only or partition.
- `frontend/src/App.tsx` — route registrations. Usually small additions; minor conflicts.
- `frontend/src/layout/AppShell.tsx` — navigation entries. Only one agent per wave should edit this file.

---

## Rule 6 — Worktrees always seed from the default branch, not your feature branch

`isolation: "worktree"` seeds from the repo's **default branch** (`main`), regardless of what the orchestrator currently has checked out. Pushing the feature branch to origin (Rule 2) is necessary but not sufficient — agents still start from `main` unless told otherwise.

Add this block as the **first step** in every agent prompt when working on a feature branch:

```bash
git fetch origin
git checkout feat/<integration-branch>
git rebase origin/feat/<integration-branch>
```

Confirm the agent is on the feature branch before it writes a single line.

---

## Rule 7 — Project-level allow lists don't apply inside worktrees

`isolation: "worktree"` places the agent inside `.claude/worktrees/agent-<id>/`. A git worktree's `.git` entry is a **file** (a pointer), not a directory. Claude Code discovers the project root by walking up the tree until it finds `.git` — so it treats the worktree as a standalone project. It then looks for `.claude/settings.json` relative to that root, finds nothing, and falls back to requiring **interactive approval** for all Bash commands. Background agents can't receive interactive approval, so they stall on any git/pytest/npm command.

**Fix:** the Bash allow list must live in **`~/.claude/settings.json`** (user-level). User-level settings apply globally regardless of how the project root is discovered.

Minimum SFBL allow list (user-level):

```json
{
  "permissions": {
    "allow": [
      "Edit", "Write",
      "Bash(git:*)", "Bash(git -C:*)",
      "Bash(pytest:*)", "Bash(python:*)", "Bash(alembic:*)", "Bash(uv:*)", "Bash(pip:*)",
      "Bash(npm:*)", "Bash(npx:*)", "Bash(node:*)",
      "Bash(docker:*)", "Bash(docker compose:*)",
      "Bash(wc:*)", "Bash(ls:*)", "Bash(cat:*)"
    ]
  }
}
```

Keep a matching `permissions.allow` in project `.claude/settings.json` for documentation, but the user-level file is the one that actually protects worktree agents.

---

## Rule 8 — Run every available static-analysis gate after each batch, not just tests

Pytest will run a module's code but may never exercise every branch; Vitest/esbuild (frontend) strips TypeScript types at runtime and never invokes `tsc`. A suite can be entirely green while the codebase has type errors or unimported-module errors.

**SFBL post-batch check sequence:**

```bash
# Backend
cd backend && pytest                                 # runtime behaviour
cd backend && python -c "from app.main import app"   # import-time sanity (catches missing router imports, etc.)
cd backend && alembic upgrade head --sql > /dev/null # migration graph sanity

# Frontend
cd frontend && npm run typecheck                     # tsc --noEmit — ONLY real type gate
cd frontend && npm run test:run                      # runtime behaviour
```

Add these to your post-batch checklist even if CI will eventually run them. Local failures are cheaper than CI round-trips, and the feedback loop is faster when an agent is still available to fix its own work.

---

## Rule 9 (SFBL-specific) — Jira state must be transitioned by the agent, not the orchestrator

Per `CLAUDE.md`, the working agent is responsible for transitioning Jira state. The orchestrator should never update Jira on behalf of a still-running agent — this produces inconsistent state when the agent then completes its own flow.

Required sequence **for each agent's ticket**:

1. **On start:** transition to `In Progress` (transition ID `21`) via `jira_transition_issue`. If the ticket has a plan comment, the agent appends "starting implementation" or similar.
2. **On completion:** run backend + frontend tests, transition to `Done` (transition ID `31`), and post a summary comment: what was implemented, key files changed, test results (pass/fail counts), any deviations from the spec.

If an agent's Jira workflow fails partway through (e.g. worktree drift prevented commit), the orchestrator **must** fix the state before launching the next batch. Leaving a ticket in `In Progress` masks the gap.

---

## Rule 10 (SFBL-specific) — Observability DoD is not optional

Any ticket that introduces or materially changes run/step/job lifecycle behaviour, Salesforce interaction flows, storage flows, retry behaviour, auth-state flows, or terminal outcomes **must** include observability updates in the same ticket.

Agent prompts that touch these surfaces must include:

```
Before writing code, work through the checklist in `docs/observability.md`:
- Any new canonical event names? Add to `app/observability/events.py`.
- Any new outcome codes? Add to `OutcomeCode`.
- New log sites use `event_name` and `outcome_code` in `extra={}`.
- Correlation IDs propagated into new async scopes via ContextVars.
- Metrics updated in `app/observability/metrics.py`.
- New execution boundaries get a custom span.
- New error paths comply with `sanitization.py` rules.
```

If observability is batched into a Wave 4 hardening ticket (as in SFBL-119's SFBL-151), the earlier tickets must still lay log sites using `event_name`/`outcome_code` even if the taxonomy constants don't yet exist — the Wave 4 ticket only sweeps the strings into constants.

---

## Quick checklist before launching a parallel wave

- [ ] User-level `~/.claude/settings.json` has the Bash allow list (Rule 7) — not just project-level
- [ ] Model selected per task: Sonnet default, Haiku for trivial, Opus only if escalated
- [ ] Feature branch created and pushed to origin
- [ ] Each agent prompt has an explicit scope restriction (`ONLY SFBL-XXX`)
- [ ] Each agent prompt names sister agents
- [ ] Each worktree-isolated prompt includes `<WORKTREE_PATH>` and the edit-path reminder
- [ ] Each prompt begins with `git fetch origin && git checkout feat/<branch> && git rebase origin/feat/<branch>`
- [ ] Agents that touch the same hot file (Rule 5) are in different batches or partitioned
- [ ] Plan to push the integration branch after each batch before launching the next
- [ ] Each agent knows: transition its Jira ticket to `In Progress` on start, `Done` on completion, with a summary comment
- [ ] PR will be raised from the feature branch after the final batch

## Quick checklist after a batch completes

- [ ] Verify each agent committed to its worktree (`git -C <worktree> log`)
- [ ] If an agent reported "nothing to commit", check main repo for unstaged changes — CWD may have drifted
- [ ] Merge worktrees to the integration branch (not `main`)
- [ ] Resolve any merge conflicts
- [ ] **Backend:** `pytest`, `python -c "from app.main import app"`, `alembic upgrade head --sql`
- [ ] **Frontend:** `npm run typecheck` (not optional — Vitest does not type-check), `npm run test:run`
- [ ] Verify each agent's Jira ticket is in `Done` with a summary comment; fix if inconsistent
- [ ] Push the integration branch to origin before launching the next batch
