---
title: Output sinks
slug: output-sinks
nav_order: 80
tags: [output, s3, storage]
required_permission: plans.manage
summary: >-
  Configure where result CSVs and logs.zip land — local filesystem or S3.
---

# Output sinks

## What this covers / who should read this

Where the Bulk Loader writes result artefacts (success / error / unprocessed
CSVs and the per-run `logs.zip`). A plan can target either the server's local
filesystem or an S3 bucket via an **Output Connection**. Authoring output
connections and attaching them to plans requires `plans.manage`.

---

## Local output sink (default)

Results are written under the server's configured output directory:

```
data/output/
└── {run_id}/
    ├── {step_id}/
    │   ├── {partition_index}_success.csv
    │   ├── {partition_index}_error.csv
    │   └── {partition_index}_unprocessed.csv
    └── logs.zip
```

In Docker, this path is a volume mount. In desktop, it's under the OS
user-data directory.

No configuration required — every plan defaults to the local sink unless you
attach an output connection.

---

## S3 output sink

Introduced by [SFBL-115](https://matthew-jenkin.atlassian.net/browse/SFBL-115).
Results stream directly to an S3 bucket using the same relative layout
(`{run_id}/{step_id}/{partition}_*.csv`) under the prefix you configure.

### Creating an S3 output connection

1. **Connections → Add S3 connection**.
2. Fill in:
   - **Name** — free-text label.
   - **Direction** — **Output** (the same connection form handles input
     and output — see [S3 connection setup](../s3-connection-setup.md) for
     the full walkthrough).
   - **Bucket**, **Prefix**, **Region**.
   - **Access key ID** + **Secret access key** — credentials with
     `s3:PutObject` + `s3:GetObject` + `s3:ListBucket` on the bucket/prefix.
3. Click **Test Connection** — the loader does a probe `HeadBucket` +
   `PutObject` of a zero-byte marker and cleans up afterwards.

AWS credentials are Fernet-encrypted at rest with the same key used for
Salesforce private keys. See
[`docs/architecture/storage.md`](../architecture/storage.md#encryption-at-rest).

### Attaching to a plan

On the plan page, set **Output connection** to the S3 connection you created.
Future runs of that plan write to S3; past runs keep pointing at whatever sink
was configured when they executed.

---

## Logs bundle (`logs.zip`)

Regardless of sink, each run produces a ZIP containing:

- Every job's success / error / unprocessed CSV.
- `run.json` with status, counts, timings.
- Per-step metadata.

The ZIP is built at run completion and written alongside the per-step CSVs.
Downloading it from the UI streams it back through the backend — see
[The Files pane](files-pane.md#downloading-the-run-log-bundle).

---

## Switching sinks

Switching a plan's output connection affects **future runs only**. You cannot
re-point an already-completed run's output.

If you need to migrate historical results from local to S3, that's an ops task
outside the app — copy `data/output/` up to S3 with `aws s3 sync` and the
layout will line up.

---

## Related

- [S3 connection setup walkthrough](../s3-connection-setup.md)
- [The Files pane](files-pane.md) — browsing results regardless of sink
- [Authoring load plans](load-plans.md)
- Architecture: [Storage & output sinks](../architecture/storage.md#output-sinks)
