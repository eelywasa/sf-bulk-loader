---
title: Bulk queries
slug: bulk-query
nav_order: 70
tags: [query, soql, bulk-api]
required_permission: plans.manage
summary: >-
  Run SOQL via the Bulk API 2.0 and chain query output into a later DML step.
---

# Bulk queries

## What this covers / who should read this

How to use `query` and `queryAll` steps to extract records from Salesforce via
the Bulk API 2.0. Authoring a query step requires `plans.manage`; running the
plan follows the usual `runs.execute` rules from
[Running a load](running-loads.md).

---

## `query` vs `queryAll`

| Operation | Includes soft-deleted / archived rows? |
|---|---|
| `query` | No — live records only. |
| `queryAll` | Yes — equivalent to the Salesforce `queryAll` REST verb. |

Both execute the same SOQL statement via the Bulk API 2.0 query endpoint and
write results to a single concatenated CSV artefact.

---

## Authoring a query step

1. On the plan page, add a step and set **Operation** to `query` or `queryAll`.
2. **Object Name** is a free-text label for this step — it's not validated
   against the SOQL.
3. Paste the SOQL into the **SOQL** field.
4. Click **Validate SOQL** — this calls Salesforce's `explain` endpoint to
   check syntax and return the query plan. No rows are fetched.
5. Save the step.

Query steps have no CSV file pattern and no partition size — the Bulk API
handles the result streaming.

---

## Output shape

One CSV file per query step, written under the run's output directory:

```
data/output/{run_id}/{step_id}/query_result.csv
```

- Header row is written once.
- If the query returns zero rows, a **header-only** file is produced.
- Columns match the SOQL `SELECT` list, in order.

The **Run detail** and **Job detail** pages show **Rows returned** and a
**Result file** link for query steps (instead of the DML success / error /
unprocessed trio).

---

## Chaining a query into a DML step

A two-step plan — query → delete — is the canonical way to bulk-delete records
matching a predicate:

1. Add a `query` step that selects `Id` for the rows you want to delete.
2. Add a `delete` step **after** it with **Input Source** set to **Local output
   files (prior run results)**.
3. Click **Browse** on the delete step and pick the query step's result CSV.

Two plans are currently required because run-specific output folders don't
exist at plan-edit time.

---

## Validating SOQL

**Validate SOQL** is the query-step equivalent of **Preview** on a DML step.
It uses the Salesforce `explain` REST endpoint:

- Returns syntax errors immediately without running the query.
- Returns the query plan (leading operation type, cardinality estimate) so you
  can spot unindexed filters before you pay for a long-running Bulk job.
- Does **not** fetch any records — safe to run against production.

---

## Related

- [Authoring load plans](load-plans.md)
- [Output sinks](output-sinks.md) — where the result CSV lands
- [Running a load](running-loads.md)
- Architecture: [Run execution](../architecture/run-execution.md)
