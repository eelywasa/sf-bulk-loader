---
title: CSV format
slug: csv-format
nav_order: 30
tags: [csv, input, format]
required_permission: plans.manage
summary: >-
  Encoding, headers, null representation, and relationship notation for
  source CSV files.
---

# CSV format

## What this covers / who should read this

The format rules for source CSVs consumed by the Bulk Loader, and how files
are discovered at run time. Read this before authoring a load plan.

---

## File requirements

The Salesforce Bulk API 2.0 is strict. The loader normalises and re-emits
files to satisfy its rules, but input files should still meet the following:

- **Encoding** — UTF-8 is preferred. Latin-1 and CP-1252 are auto-detected and
  re-encoded to UTF-8. A UTF-8 BOM is tolerated.
- **Line endings** — LF (`\n`) in the output. The loader re-emits with LF even
  if the input uses CRLF.
- **Headers** — the first row contains **Salesforce field API names**
  (case-sensitive, exact match) — `FirstName`, not `First Name`.
- **Nulls** — use `#N/A` (literal, uppercase) to explicitly null a field.
  Empty cells are sent as empty strings, not null.
- **Headers preserved on every partition** — the loader splits large CSVs
  into partitions; each partition gets a copy of the header row.

---

## Relationship notation (lookups by external ID)

For child records that reference a parent via a **Salesforce external ID**,
use `ParentObject.ExternalIdField__c` as the column header:

```csv
FirstName,LastName,Email,Account.ExternalId__c
Jane,Doe,jane@example.com,ACCT-001
John,Smith,john@example.com,ACCT-002
```

This avoids a round-trip lookup at load time — Salesforce resolves the parent
by external ID as part of the Bulk API write. If the external ID is missing or
unknown, the row fails validation and lands in the error CSV.

---

## Where the files live

| Profile | Input location |
|---|---|
| `desktop` | `input/` under the OS user-data directory (platform-specific — see the desktop deployment guide) |
| `self_hosted` (default) | `./data/input/` on the host, mounted read-only at `/data/input` in the container |
| `self_hosted` (S3) | Input S3 bucket configured via an **Input Connection** in the UI |
| `aws_hosted` | Input S3 bucket (local filesystem is not used) |

Each load step's **CSV File Pattern** is a glob resolved at step execution
time. It runs only within the configured input location — `..` traversal and
absolute paths are rejected.

---

## Glob patterns

| Pattern | Matches |
|---|---|
| `accounts.csv` | A single named file |
| `accounts_*.csv` | Every file starting with `accounts_` |
| `subdir/**/*.csv` | Every `.csv` file under `subdir/`, recursively |

Multiple matches are processed in alphabetical order and each file goes
through partitioning independently. The resulting jobs execute concurrently
subject to the plan's `max_parallel_jobs` setting.

---

## Partitioning

Files larger than the configured partition size are split automatically:

- **Default partition size**: 10 000 records (configurable via
  **Settings → Partitioning**).
- **Per-step override**: set on the step when partition size should differ for
  that object.
- **Ceiling**: Salesforce enforces a 150 MB per-upload limit — the loader
  caps the partition size below this.
- **Memory**: streaming — never more than one partition's rows held in memory.

A 50 000-row CSV at partition size 10 000 becomes 5 Bulk API jobs, tracked
individually in the run.

---

## Related

- [Authoring load plans](load-plans.md)
- [Setting up a Salesforce connection](salesforce-connection.md)
- [Running a load](running-loads.md)
- Architecture: [Storage & partitioning](../architecture/storage.md)
