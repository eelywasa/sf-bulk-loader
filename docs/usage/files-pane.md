---
title: The Files pane
slug: files-pane
nav_order: 60
tags: [files, results, previews]
required_permission: files.view
summary: >-
  Browse input and output files, preview CSV content, and download result
  artefacts.
---

# The Files pane

## What this covers / who should read this

How to inspect input CSVs and job result files via the **Files** pane. The
pane has two permission tiers — `files.view` to browse and see metadata,
`files.view_contents` to actually read or download the bytes — the second
tier exists so PII stays behind a separate gate.

---

## Permission gating

| Permission | What it unlocks |
|---|---|
| `files.view` | See the file tree, names, sizes, and modification times. |
| `files.view_contents` | Download the CSV, preview rows, download the per-run `logs.zip`. |

The default `viewer` profile has `files.view` but **not**
`files.view_contents` — it can see that files exist without reading the
records inside them. Enforced both server-side (`require_permission`) and in
the UI (the **Logs** tab on the job detail page and the **Download logs**
card on the run detail page are hidden when the permission is absent).

---

## Output layout

Local output sink:

```
data/output/
└── {run_id}/
    ├── {step_id}/
    │   ├── {partition_index}_success.csv
    │   ├── {partition_index}_error.csv
    │   └── {partition_index}_unprocessed.csv
    └── logs.zip
```

S3 output sink uses the same layout under the configured prefix.

Result file types per DML job:

| File | Contains |
|---|---|
| `success.csv` | Records Salesforce accepted. |
| `error.csv` | Records Salesforce rejected, with per-row error messages. |
| `unprocessed.csv` | Records never processed — rare; typically only when the job was aborted mid-flight. |

Query steps write a single concatenated CSV per step (not split by
partition). A header-only file is produced when the query returns zero rows.

---

## Previewing result CSVs

On the **Job detail** page (reachable from the run detail's step list):

1. Open the **Logs** tab.
2. Pick **Success**, **Error**, or **Unprocessed**.
3. The first page of rows loads inline. Scroll or paginate to see more.

Previews support column filters so you can narrow large error files down to
specific Salesforce error codes. Limit is 500 rows per page.

Previews require `files.view_contents`.

---

## Downloading result CSVs

On the same Logs tab, click **Download** next to the file you want. The
browser downloads the CSV directly from the backend — the bytes are streamed
from the configured output sink (local or S3).

Download endpoints:

- `/api/jobs/{id}/success-csv`
- `/api/jobs/{id}/error-csv`
- `/api/jobs/{id}/unprocessed-csv`

All three require `runs.view` + `files.view_contents`.

---

## Downloading the run log bundle

The **Download logs** card on the run detail page produces a ZIP containing:

- Every job's success / error / unprocessed CSV.
- A `run.json` summary of the run (status, counts, timings).
- Per-step metadata.

Endpoint: `/api/runs/{id}/logs.zip`. Requires `runs.view` +
`files.view_contents`.

---

## Related

- [Running a load](running-loads.md)
- [Output sinks](output-sinks.md)
- Architecture: [Storage & output sinks](../architecture/storage.md#output-sinks)
