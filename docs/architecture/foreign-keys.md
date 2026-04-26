# Foreign Key Inventory

## What this covers / who should read this

A complete inventory of every foreign key declared in the schema, its
`ondelete=` action, and the intended runtime behaviour. Read this before
changing any model relationship, writing a parent-table delete path, or
auditing data integrity in a production database.

The inventory exists because of a latent runtime bug fixed on 2026-04-25
(commit `c554767`): the SQLite `PRAGMA foreign_keys=ON` listener in
`app/database.py` had been gated by `isinstance(dbapi_connection,
sqlite3.Connection)`, but `aiosqlite` wraps the connection in
`AsyncAdapt_aiosqlite_connection` so the body never ran. Every
`ON DELETE CASCADE` and `ON DELETE SET NULL` declared in the schema was
therefore a no-op on SQLite at runtime. The PRAGMA is now applied via a
dialect-name gate, and a boot-time assertion in
`app/database.py::assert_sqlite_fk_enforcement_active` re-checks the value
on a fresh connection at lifespan startup so the same silent failure cannot
recur. SFBL-270 is the systematic hardening pass; this file is its
deliverable.

## Cascade legend

- **CASCADE** — deleting the parent row deletes this child row.
- **SET NULL** — deleting the parent row nulls this column on the child
  (column must be `nullable=True`).
- **RESTRICT** — deleting the parent row is rejected if any child row
  references it. Both Postgres and SQLite enforce this strictly when
  `foreign_keys=ON`.
- **NO ACTION** — same as RESTRICT for our purposes (we don't use deferred
  constraints anywhere). Declared by omission of `ondelete=`.

## Inventory

The table below lists every `ForeignKey(...)` declared under
`backend/app/models/`. The DB-layer column flags whether a runtime FK
constraint actually exists: every row in this inventory is enforced at the
DB layer on both SQLite and Postgres unless explicitly noted.

| # | Source | Target | ondelete | Intent | DB-enforced |
|---|---|---|---|---|---|
| 1 | `user.profile_id` | `profiles.id` | NO ACTION | RESTRICT-like: deleting a profile that any user is assigned to is an error; reassign users first. | yes |
| 2 | `user.invited_by` | `user.id` | SET NULL | Self-referential. Invitee survives if the inviter is deleted; the audit pointer is dropped. | yes |
| 3 | `user_totp.user_id` | `user.id` | CASCADE | TOTP enrolment is per-user; deletion follows the user. | yes |
| 4 | `user_backup_code.user_id` | `user.id` | CASCADE | Backup codes follow the user. | yes |
| 5 | `invitation_tokens.user_id` | `user.id` | CASCADE | Pending invitations cleared when the user row is removed. | yes |
| 6 | `password_reset_token.user_id` | `user.id` | CASCADE | Reset tokens follow the user. | yes |
| 7 | `email_change_token.user_id` | `user.id` | CASCADE | Pending email-change requests follow the user. | yes |
| 8 | `login_attempt.user_id` | `user.id` | SET NULL | Audit row is preserved across user deletion; the FK is nulled so the attempt remains queryable for the audit trail. | yes |
| 9 | `notification_subscription.user_id` | `user.id` | CASCADE | Subscriptions are user-owned and follow the user. | yes |
| 10 | `notification_subscription.plan_id` | `load_plan.id` | CASCADE | A subscription scoped to a deleted plan has no remaining target. | yes |
| 11 | `notification_delivery.subscription_id` | `notification_subscription.id` | CASCADE | Delivery rows are subscription-scoped. | yes |
| 12 | `notification_delivery.run_id` | `load_run.id` | SET NULL | NULL for `/test` dispatches; preserved across run deletion since the delivery is itself the audit record. | yes |
| 13 | `notification_delivery.email_delivery_id` | `email_delivery.id` | SET NULL | Cross-table pointer to email-side accounting; preserved if the email row is reaped before the notification. | yes |
| 14 | `profile_permissions.profile_id` | `profiles.id` | CASCADE | Permission grants are profile-scoped. | yes |
| 15 | `load_plan.connection_id` | `connection.id` | RESTRICT | Salesforce credential rows are referenced by historical runs; deletion must be explicit on the operator side. | yes |
| 16 | `load_plan.output_connection_id` | `input_connection.id` | SET NULL | Output sink is optional; if the storage connection is deleted, the plan reverts to no-output. | yes |
| 17 | `load_step.load_plan_id` | `load_plan.id` | CASCADE | Steps are owned by their plan. | yes |
| 18 | `load_step.input_from_step_id` | `load_step.id` | SET NULL | SFBL-166 wires a step's input to an upstream query step's run-scoped output; the FK self-references and a deleted upstream null-resets the wiring. Application-layer validation rejects the resulting orphan before the next plan save. | yes |
| 19 | `load_run.load_plan_id` | `load_plan.id` | RESTRICT | Run history is preserved across plan modifications; deleting a plan with runs requires explicit run cleanup. | yes |
| 20 | `load_run.retry_of_run_id` | `load_run.id` | NO ACTION | Self-referential pointer to the run this is a retry of. RESTRICT-equivalent: if the original run is deleted while a retry exists, the delete is rejected. (Run deletion is rare and gated by RBAC; the strict default is intentional.) | yes |
| 21 | `job_record.load_run_id` | `load_run.id` | CASCADE | Per-partition job records follow the run. | yes |
| 22 | `job_record.load_step_id` | `load_step.id` | RESTRICT | Steps with historical job records cannot be deleted; the orchestrator's sequence-level invariants depend on the step row remaining. | yes |

### Non-FK reference columns (intentional)

- `load_step.input_connection_id` — declared as `String(36)` with **no** FK
  constraint. The column carries either a real `input_connection.id` UUID
  or one of the reserved sentinel values `""`, `"local"`, `"local-output"`
  (see migration 0014 and `app/services/input_storage.get_storage`).
  Validation lives at the schema/service layer
  (`api/load_steps._validate_input_connection_direction`); a DB-level FK
  would block the sentinels.

## Postgres inline-FK gap (history)

Migration 0028 (`add_step_name_and_input_from_step`) initially added the
`load_step.input_from_step_id` column inline with `ForeignKey(...)`, which
emitted the FK on SQLite (via `batch_alter_table`) but silently dropped the
constraint on Postgres because the inline form is not honoured by Postgres
in `op.add_column` outside a batch operation. Fixed in the same merge
(commit `b924017`) by issuing an explicit `op.create_foreign_key` after the
column add. When adding new FK columns in future migrations, prefer the
two-step `add_column` then `create_foreign_key` pattern over the inline
form.

## Operator runbook: scanning for orphans

If you have a long-running SQLite database that predates the PRAGMA fix,
some `CASCADE` deletes that should have removed child rows may have left
orphans behind. Scan for them with:

```bash
python scripts/scan_fk_orphans.py --db /path/to/sf_bulk_loader.db
```

The script is read-only. It walks each FK above and counts rows whose
non-NULL FK column points at a missing parent. A clean DB prints `0` for
every FK and exits with status `0`. If counts are non-zero, file an issue
referencing the FK and the count; cleanup is decided per-case (drop the
orphan, or null the column for SET-NULL semantics) rather than automated,
because the right action depends on whether the orphan represents
operator-visible history.

## Tests

Per-cascade behaviour is asserted in
`backend/tests/test_fk_cascades.py`. The file has one parametrised test
per declared `CASCADE` and per declared `SET NULL` (rows 3–13, 16–18, 21
above), one negative-control test that disables `PRAGMA foreign_keys` on
its own session and asserts the cascade does NOT fire (guarding against
regression of the listener's dialect-name gate), and a coverage-completeness
test that walks `Base.metadata` and fails if a new `CASCADE` / `SET NULL`
FK is introduced without a paired test entry.
