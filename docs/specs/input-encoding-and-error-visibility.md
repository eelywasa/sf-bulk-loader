# Input Encoding Robustness & Run Error Visibility

**Status:** Live spec — ticketed as epic **SFBL-400**. Drafted 2026-08-17 from a
live production incident on the plan `Test 1 - Account`
(`bbc8dd57-25b7-42bf-8581-629c24776d9d`). Three independent defects were
identified; this file is the locked design for fixing all three.
See [Story breakdown](#story-breakdown) for the child tickets.

All line references were verified against `origin/main` at `2a351ba`.

---

## Background

Fourteen consecutive runs of the plan `Test 1 - Account` failed between
2026-08-07 09:48 and 14:16. Every run died in **~0.3 s having created zero
jobs** — no Salesforce Bulk API call was ever made. Two error signatures were
recorded, in two distinct time windows:

| Window (2026-08-07) | Recorded error |
|---|---|
| 09:48 → 13:37 | `'utf-8' codec can't decode byte 0xe1 in position 7475: invalid continuation byte` |
| 13:40 → 14:16 | `'charmap' codec can't decode byte 0x81 in position 6362: character maps to <undefined>` |

The investigation found three separate defects. Only the first blocks the
load; the second is why the incident took disproportionately long to
diagnose; the third is latent and would have surfaced immediately after the
first was fixed.

| # | Defect | Severity |
|---|---|---|
| 1 | Encoding detected from a 64 KiB prefix, applied to the whole stream | Blocker — loads fail |
| 2 | `RunErrorSummary` silently discards two of the five error keys written | High — fatal errors invisible in UI/API |
| 3 | `LoadStep.object_name` accepts an empty string | Medium — latent, blocks on fix of #1 |

---

## Defect 1 — Prefix-sampled encoding applied to a whole stream

### Mechanism

`S3InputStorage.open_text` (`backend/app/services/input_storage.py:893`)
reads the first 64 KiB of the object, infers an encoding from **that sample
only**, then wraps the **entire** stream with it:

```python
sample = body.read(65536)                      # detection window
enc = detect_encoding_from_bytes(sample)
raw = _S3StreamingBodyReader(body, sample)
buffered = io.BufferedReader(raw, buffer_size=65536)
return io.TextIOWrapper(buffered, encoding=enc, newline="")
```

`detect_encoding_from_bytes` walks `_ENCODING_CANDIDATES`
(`input_storage.py:39`):

```python
_ENCODING_CANDIDATES: tuple[str, ...] = ("utf-8-sig", "cp1252", "latin-1")
```

The offending file, `Account_sample.csv`, is **696,790 bytes** — roughly ten
times the detection window. Bytes beyond the first 64 KiB are never sampled,
so any encoding-significant byte in the remaining ~630 KB can invalidate the
guess mid-read.

Both observed signatures follow directly, and were reproduced against the
real candidate list:

- Prefix is clean ASCII → `utf-8-sig` selected → a later `0xe1` is an invalid
  UTF-8 continuation byte → **`'utf-8' codec can't decode byte 0xe1`**.
- Prefix contains `0xe1` → UTF-8 rejected, `cp1252` selected → a later `0x81`
  (one of five bytes undefined in cp1252: `0x81 0x8D 0x8F 0x90 0x9D`) →
  **`'charmap' codec can't decode byte 0x81`**.

The two time windows correspond to the S3 object being replaced around 13:40
— a different prefix yields a different guess and therefore a different
downstream failure.

**The safe fallback is unreachable.** `latin-1` never raises on any byte
sequence, but it is only selected if the **sample** fails to decode. Since
the sample always decodes (that is how the encoding was chosen), the
terminal fallback can never be reached by a mid-stream failure.

`LocalInputStorage.open_text` (`input_storage.py:641`) has the same flaw via
`detect_encoding`, which samples `file_path.read_bytes()[:65536]`.

### Why the run dies rather than degrading

Two call sites open the file. They behave differently:

| Call site | Wrapped? | Effect |
|---|---|---|
| Preflight row pre-count — `run_coordinator.py:577` | Yes — broad `except Exception` | Non-fatal `preflight_warnings` entry |
| Step partitioning — `step_executor.py:227` | **No** | `UnicodeDecodeError` propagates and fails the run |

`step_executor.py:227` is bare:

```python
for rel_path in rel_paths:
    with storage.open_text(rel_path) as fh:
        for chunk in _partition(fh, _effective_partition_size):
            partitions.append((len(partitions), chunk))
```

The exception unwinds into the run coordinator's broad step-loop handler
(`run_coordinator.py:763`), which marks the run failed with
`error_summary={"unexpected_exception": str(exc)}` — a key the API then
discards (see Defect 2).

### Misleading byte offsets

The reported positions (`7475`, `6362`) are **chunk-relative**, not
file-relative — `TextIOWrapper` reports the offset within the buffer chunk it
was decoding. Both values are smaller than the 64 KiB sample window, which
makes the failure look like it occurred inside the sampled region and
actively misdirects diagnosis. Any error we surface must report a
**file-absolute byte offset**.

### Design

**D1.1 — Fail loudly, not silently.** Do *not* fall back to `latin-1` on a
mid-stream failure. A file that is 99% valid UTF-8 with one corrupt byte
would be re-read entirely as latin-1, mangling every non-ASCII character in
the file and writing mojibake into Salesforce. For a data loader, silent
lossy decoding across a whole file is worse than a clear error. The default
on a mid-stream decode failure is to **abort the step with an actionable
error**.

**D1.2 — Convert to `InputStorageError`.** Catch `UnicodeDecodeError` at the
storage boundary and re-raise as `InputStorageError`. This type is already
handled explicitly in the run coordinator (`run_coordinator.py:730`) and maps
to the `storage_error` key, which *is* declared in `RunErrorSummary` — so the
error becomes visible without depending on Defect 2 being fixed. Message
must include: file name, detected encoding, offending byte value, and
**file-absolute** byte offset.

**D1.3 — Explicit encoding override.** Add an optional `encoding` field to
the input connection, and an optional per-step override. When set, detection
is skipped entirely. This is the operator's escape hatch when they know the
source encoding and do not want to depend on a guess.

Resolution order: step override → connection setting → auto-detect.

**D1.4 — Opt-in lossy mode.** Add an `on_decode_error` policy with values
`fail` (default) and `replace`. Under `replace`, decoding uses
`errors="replace"` and the run records a **count of replaced characters** per
file as a run-level warning. Operators may knowingly accept lossy decoding;
they may not do so accidentally.

**D1.5 — Wrap the partition call site.** `step_executor.py:227` must handle
`InputStorageError` so the failure is attributed to the step rather than
escaping as an unhandled exception.

**D1.6 — Fix `LocalInputStorage` identically.** Same sampling flaw, same fix.

**D1.7 — Bound the local sample read.** Added 2026-08-17 during ticketing;
not part of the original incident analysis. `detect_encoding`
(`input_storage.py:134`) calls `file_path.read_bytes()` and *then* slices off
the first 64 KiB — so the whole file is loaded into memory before the sample
is taken. On the 16 MB files already present in the input tree that is 16 MB
of needless allocation per `open_text` call, and it scales with input size.
Replace with a bounded read. Folded into S1 because D1.6 already touches this
function.

Out of scope: full-file pre-scanning for encoding detection. The input tree
contains files up to 16 MB (`ContactPointTypeConsent_sample.csv`); a second
full read or GET per file is not justified when D1.2/D1.3 make the failure
actionable and avoidable.

---

## Defect 2 — `RunErrorSummary` silently discards error keys

`run_coordinator.py` writes **five** distinct keys into
`LoadRun.error_summary`:

```
auth_error   output_storage_error   preflight_warnings   storage_error   unexpected_exception
```

`RunErrorSummary` (`backend/app/schemas/load_run.py:26`) declares **four**,
with `extra="ignore"`:

```python
class RunErrorSummary(BaseModel):
    auth_error: Optional[str] = None
    storage_error: Optional[str] = None
    circuit_breaker: Optional[str] = None
    preflight_warnings: Optional[List[PreflightWarning]] = None

    model_config = ConfigDict(extra="ignore")
```

`unexpected_exception` and `output_storage_error` are written to the database
and then **silently dropped on the way out**. Note also that
`circuit_breaker` is declared but is written via `_merge_run_error_summary`
rather than the `error_summary=` kwarg — it is correct, but the mismatch
between the two write paths is what makes the drift easy to miss.

During this incident the operator saw only a non-fatal `preflight_warnings`
entry, while the actual fatal error was invisible. The preflight warning
happened to describe the same underlying decode failure, which was
fortunate; had the fatal error been unrelated, there would have been no
signal at all.

### Design

**D2.1 — Declare the missing fields.** Add `unexpected_exception:
Optional[str]` and `output_storage_error: Optional[str]` to
`RunErrorSummary`.

**D2.2 — Prevent recurrence with a contract test.** Adding two fields fixes
today's drift but not the class of bug. Add a test that statically collects
every key written to `error_summary` in `run_coordinator.py` and asserts each
is a declared field on `RunErrorSummary`. This is the substantive fix; D2.1
alone will drift again.

**D2.3 — Surface it in the UI.** The run detail page must render whichever
error keys are populated. A run in `failed` state with no visible reason is
the failure mode being corrected.

---

## Defect 3 — Empty `object_name` accepted on load steps

Step `ec7bb0c2-17e8-4231-b0ee-3b8ce1fb8d8f` ("Account v2", created
2026-08-07 14:15) on the incident plan has:

```json
{"sequence": 2, "name": "Account v2", "object_name": "", "operation": "upsert"}
```

`LoadStepBase.object_name` (`backend/app/schemas/load_step.py:99`) is a bare
`str` with no length constraint, so `""` passes validation. The model column
(`models/load_step.py:41`) is `nullable=False`, which an empty string
satisfies. The step is currently masked because the run dies at Defect 1
first; it would fail at Bulk API job creation as soon as Defect 1 is fixed.

### Design

**D3.1 — Constrain the field.** `object_name` becomes a constrained string
with `min_length=1` after whitespace trimming, mirroring the existing
`_normalize_name` treatment of `LoadStep.name`. Applies to both
`LoadStepBase` and `LoadStepUpdate` (where it is `Optional[str]` — `None`
stays valid, `""` does not).

**D3.2 — Do not migrate silently.** Existing rows with an empty
`object_name` must not be auto-populated or deleted. Surface them as a
validation error in the plan editor so the operator supplies the correct
object. A one-line startup log naming affected step IDs is sufficient.

---

## Observability

Per the Observability Definition of Done in `CLAUDE.md`, this work changes
storage flows and terminal outcomes.

### Existing machinery to reuse (do not duplicate)

The input-storage observability layer already landed independently of this
spec. S1 must build on it rather than introduce parallel names:

- **`StorageEvent.INPUT_FAILED`** (`"storage.input.failed"`,
  `app/observability/events.py:135`) already exists and is the correct
  `event_name` for a decode failure. **No new event name is required.**
- **`_s3_outcome_code`** (`input_storage.py:396`) maps a botocore
  `ClientError` to `RATE_LIMITED` (throttling) or `STORAGE_ERROR`
  (everything else). It handles *transport* failures only — a
  `UnicodeDecodeError` never reaches it, so decode failures currently have
  no outcome-code path at all.
- `OutcomeCode` has `storage_error` ("input storage access failure") but
  **no decode-specific code**.

### D4.1 — A decode failure gets its own outcome code

Add `INPUT_DECODE_ERROR = "input_decode_error"` to `OutcomeCode`, and
document it in the class docstring alongside `storage_error`.

Rationale: `storage_error` means *the source is unreachable* — S3 is down,
credentials are wrong, the key is missing. A decode failure means *the source
was read perfectly and its bytes are not what we expected*. These have
different owners and different remedies: the first pages an engineer, the
second is a data problem the operator fixes by re-encoding a file. Collapsing
them into one code makes it impossible to alert on infrastructure health
without also firing on malformed customer data.

### D4.2 — The two axes are independent

The `error_summary` key and the observability outcome code are **separate
axes** and must not be conflated:

| Axis | Value | Purpose |
|---|---|---|
| `RunErrorSummary` key | `storage_error` | UI/API visibility — reuses a field that is already declared, so it works regardless of Defect 2 |
| `OutcomeCode` | `input_decode_error` | Logs, metrics, alerting — distinguishes malformed data from unreachable storage |

D1.2 routes the failure through `InputStorageError` specifically so it lands
in the already-declared `storage_error` key. That is a **visibility**
decision and does not imply the outcome code must also be `storage_error`. A
decode failure therefore logs with `event_name=storage.input.failed`,
`outcome_code=input_decode_error`, while populating
`error_summary.storage_error`.

### Remaining requirements

- The decode-failure log site must carry `event_name`, `outcome_code`,
  `run_id`, `step_id`, and the resolved file path.
- Under `on_decode_error=replace`, emit a metric for replaced-character
  count so lossy decoding is measurable rather than invisible.
- Confirm the new error message complies with `sanitization.py` — file paths
  and byte values are safe; **decoded record content must never appear in the
  error message**.
- Update the storage-flow section of `docs/observability.md`, which
  `_s3_outcome_code` cites as its reference.

---

## Documentation impact

Per the Epic DoD, shipping this requires:

- `docs/usage/` — document the encoding override and `on_decode_error`
  policy on the relevant input-connection topic, with frontmatter
  `required_permission` checked against `backend/app/auth/permissions.py`.
- `docs/architecture/` — update any description of input storage decoding.
- `docs/specs/implemented/input-storage-spec.md` — add a banner noting this
  spec supersedes its encoding-detection section.
- Run `node frontend/scripts/check-help-links.mjs` before pushing.

---

## Story breakdown

Suggested as **three stories under one bug-fix epic**. They are independent
and can be worked in parallel, but S1 is the only one that unblocks the
failing load.

| Story | Ticket | Scope |
|---|---|---|
| S1 — Stream-safe input decoding | SFBL-401 | D1.1–D1.7 + D4.1–D4.2: `InputStorageError` conversion with file-absolute offsets, encoding override on connection + step, `on_decode_error` policy, wrap `step_executor.py:227`, fix `LocalInputStorage`, `INPUT_DECODE_ERROR` outcome code reusing the existing `StorageEvent.INPUT_FAILED`, migration for the new fields, unit + integration tests |
| S2 — Run error visibility | SFBL-402 | D2.1–D2.3: add the two missing `RunErrorSummary` fields, contract test enumerating `error_summary` keys, render populated keys on the run detail page |
| S3 — Reject empty `object_name` | SFBL-403 | D3.1–D3.2: `min_length=1` after trim on create + update schemas, plan-editor validation error for existing empty rows, tests |

### Acceptance criteria

**S1**
- A file whose first 64 KiB decodes cleanly but which contains an invalid
  byte later fails with `InputStorageError`, not `UnicodeDecodeError`.
- The error message names the file, the encoding attempted, the byte value,
  and a **file-absolute** byte offset.
- The run terminates with `storage_error` populated in `error_summary`.
- Setting an explicit encoding on the connection bypasses detection.
- `on_decode_error=replace` completes the load and records a
  replaced-character count.
- The failure logs with `event_name=storage.input.failed` and
  `outcome_code=input_decode_error` — **not** `storage_error` — while
  `error_summary.storage_error` is populated for the UI. No new
  `StorageEvent` member is added.
- Equivalent coverage for `LocalInputStorage`.
- `detect_encoding` no longer reads the whole file into memory; a test asserts
  the bytes read are bounded by the sample size for a file much larger than
  the detection window.

**S2**
- A run failing via the broad step-loop handler exposes
  `unexpected_exception` through `GET /api/runs/{id}`.
- The contract test fails if a new `error_summary` key is added to
  `run_coordinator.py` without a matching `RunErrorSummary` field.
- The run detail page renders every populated error key.

**S3**
- `POST`/`PUT` of a load step with `object_name` of `""` or whitespace
  returns 422.
- `object_name: None` on update remains valid (partial update semantics).
- Existing rows with an empty `object_name` surface a validation error in the
  plan editor and are not silently rewritten.

---

## Immediate operator workaround

Independent of the fix, the blocked load can be unblocked by re-encoding the
source file as UTF-8 and re-uploading to
`s3://ucas-ani-prod-infrastructure-salesforce/test_inputs/`. Offending bytes
can be located with:

```bash
iconv -f UTF-8 -t UTF-8 Account_sample.csv > /dev/null
```

The sample data appears to be generated/obfuscated names, so the invalid
bytes are most likely mangled accented characters from the generator rather
than meaningful content — worth confirming before re-encoding, since a
`latin-1` round-trip would preserve them correctly if they are genuine.
