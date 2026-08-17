# Input Encoding Robustness & Run Error Visibility

**Status:** Live spec — ticketed as epic **SFBL-400**. Drafted 2026-08-17 from a
live production incident on the plan `Test 1 - Account`
(`bbc8dd57-25b7-42bf-8581-629c24776d9d`). Three independent defects were
identified; this file is the locked design for fixing all three.
See [Story breakdown](#story-breakdown) for the child tickets.

All line references were verified against `origin/main` at `2a351ba`, and
re-verified at `e1cd3d4` during the QA pass below.

**Revision 2 (2026-08-17)** — revised after a three-lens QA pass against the
codebase. The diagnosis in Defects 1–3 was confirmed in full; the *designs*
had material errors. Changes:

- D1.2 could not be implemented as written — `open_text` returns a handle and
  decoding happens lazily in the caller, so there is no boundary to catch at.
  Now specifies a decoding stream wrapper (D1.8) and a dedicated
  `InputDecodeError` subclass.
- D1.5 contradicted D1.2 and its own AC — restated as attribute-and-re-raise.
- Call-site inventory corrected: **six** `open_text` consumers, not two. One
  (`api/load_steps.py:442`) would have *regressed to a silent zero-row
  preview* under the original D1.2.
- D1.6 extended to `preview_file`, which bypasses `open_text` entirely.
- D1.3 could not reach the default input source, which has no
  `InputConnection` row — app-setting tier added.
- D1.4 given a defined home and a workable counting mechanism.
- New D1.9 covers the *silent* sibling of the incident failure.
- Defect 2: **six** error keys, not five — `unknown_exit` was missed.
- D2.2 mechanism replaced with a strictly better one.
- D3.1 as written would have 500'd every plan containing the bad row,
  defeating D3.2.

Corrected line reference: `_s3_outcome_code` is at `input_storage.py:376`
(previously cited as `:396`).

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

**Revision 2 — corrected.** There are **six** `open_text` consumers, not two.
The original table listed only the two on the run path and led the design to
miss four, one of which *regresses* under the naive fix.

| # | Call site | Today | Under D1.2 as originally written |
|---|---|---|---|
| 1 | Preflight pre-count — `run_coordinator.py:577` | Broad `except Exception` at `:598` → warning with `unexpected_exception` | `except InputStorageError` at `:584` fires **first** → warning with `storage_error` |
| 2 | Step partitioning — `step_executor.py:227` | **Unhandled** → run fails with `unexpected_exception` | Run fails with `storage_error` ✅ |
| 3 | Retry partitions — `csv_processor.py:368` | Escapes → 500 from the retry-step route (`load_runs.py:138` has no handler) | Still 500, better message |
| 4 | Step preview — `api/load_steps.py:438` | `UnicodeDecodeError` is a `ValueError`, so the `except (FileNotFoundError, InputStorageError, OSError)` at `:442` does **not** catch it → 500 | **Caught and merely logged → `row_count=0`.** A corrupt file previews as "0 rows" with no operator signal — a *new silent failure*, in the very spec whose premise is that silent failures caused the incident |
| 5–6 | S3 `preview_file` → `utility.py:175`/`:282` | 500 | 400 carrying the decode message ✅ |

Note the preflight site has **two** handlers — `except InputStorageError` at
`:584` and `except Exception` at `:598`. The original table's "broad
`except Exception`" is imprecise, and the imprecision is load-bearing: after
D1.2 it is the *first* that fires, which is what breaks the `input_decode_error`
outcome-code requirement unless D1.2a is applied.

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

Decisions are ordered by topic, not by number: D1.1–D1.6 are the original
set, and D1.7–D1.9 (added later) sit next to the decision each one supports.
The numbers are stable identifiers — do not renumber.

**D1.1 — Fail loudly, not silently.** Do *not* fall back to `latin-1` on a
mid-stream failure. A file that is 99% valid UTF-8 with one corrupt byte
would be re-read entirely as latin-1, mangling every non-ASCII character in
the file and writing mojibake into Salesforce. For a data loader, silent
lossy decoding across a whole file is worse than a clear error. The default
on a mid-stream decode failure is to **abort the step with an actionable
error**.

**D1.2 — Convert to `InputDecodeError`.** *Revised.* Re-raise decode failures
as `InputDecodeError`, a **subclass of `InputStorageError`** carrying
`encoding`, `byte_value`, `byte_offset`, and `path` as structured attributes.

Subclassing means it still flows through the existing
`except InputStorageError` handler at `run_coordinator.py:730` and lands in
the `storage_error` key — which *is* declared in `RunErrorSummary`, so
visibility does not depend on Defect 2 being fixed. Precedent:
`StepReferenceResolutionError` subclasses `InputStorageError` for exactly this
reason (`step_executor.py:172-175`).

The original wording — "convert to `InputStorageError`" — was wrong in two
ways. First, there is no boundary to catch at (see D1.8). Second, collapsing
into the bare parent type destroys the distinction D4.1 exists to create: both
handlers key on exception *type* and hardcode `outcome_code=STORAGE_ERROR`
(`run_coordinator.py:584-597` and `:730-742`), so a decode failure would log
`storage_error` twice and the `input_decode_error` outcome code would be
unreachable.

**D1.2a — Both handlers must branch on the subclass.** `run_coordinator.py`
at `:584` and `:730` must test for `InputDecodeError` before the generic
`InputStorageError` path and use `outcome_code=INPUT_DECODE_ERROR`. Without
this, D4.1 is decorative.

Message must include file name, attempted encoding, offending byte value, and
**file-absolute** byte offset. Structured attributes carry the same data for
the log site so the message never has to be re-parsed.

**D1.8 — A decoding stream wrapper is required.** *New.* `open_text` **returns
a handle** (`input_storage.py:663`, `:932`); decoding happens lazily inside the
caller's read loop — for the fatal path, inside `_partition(fh, ...)` at
`step_executor.py:228`. There is therefore no `try` at the storage boundary
that a `UnicodeDecodeError` ever passes through, and a literal reading of the
original D1.2 would catch nothing at all.

Both providers must return a shared `_DecodingTextStream` wrapper that owns
the read loop: read bounded byte chunks, feed them through
`codecs.getincrementaldecoder(enc)`, and track the cumulative byte offset.
It must implement the text-IO surface actually consumed downstream —
`__iter__`, `readline`, `read`, `__enter__`/`__exit__`, `close` — since
callers pass it to `csv.reader` and `csv.DictReader`.

This wrapper is load-bearing for three separate requirements and should be
built once:

1. It is the only place a `UnicodeDecodeError` can be caught (D1.2).
2. It makes the **file-absolute** offset exact *by construction*. This matters:
   `TextIOWrapper.tell()` raises `UnsupportedOperation` on a non-seekable S3
   stream, so the offset cannot be recovered after the fact. Deriving it
   arithmetically from `exc.object`/`exc.start` was verified to work but
   depends on CPython buffering internals and breaks if anything peeks or
   issues sized reads. Owning the loop avoids both traps. Mind the 3-byte
   `utf-8-sig` BOM when computing offsets.
3. It is where D1.4's replacement counter lives.

**D1.3 — Explicit encoding override.** Add an optional `encoding` field to
the input connection, and an optional per-step override. When set, detection
is skipped entirely. This is the operator's escape hatch when they know the
source encoding and do not want to depend on a guess.

*Revised — a connection-level field cannot reach the source that actually
failed.* `get_storage` (`input_storage.py:1008-1017`) resolves `None`, `""`,
`"local"` and `"local-output"` to the default storage with **no
`InputConnection` row at all**, so there is nowhere to hang the field. A
deployment-level default is therefore required as well: add a DB-backed app
setting following the `settings_service` pattern already used for
`default_partition_size` (`step_executor.py:218-223`).

Resolution order: step override → connection setting → app setting →
auto-detect.

The encoding name must be validated at write time. Today an invalid value
would surface only at read time, because `detect_encoding_from_bytes` swallows
`LookupError` (`input_storage.py:115`).

**D1.4 — Opt-in lossy mode.** Add an `on_decode_error` policy with values
`fail` (default) and `replace`. Under `replace`, decoding continues past bad
bytes and the run records a **count of replaced characters** per file as a
run-level warning. Operators may knowingly accept lossy decoding; they may
not do so accidentally.

*Revised — home and mechanism now specified.* `on_decode_error` lives
alongside `encoding` with the identical resolution order (step → connection →
app setting → default `fail`). The original text specified neither, making the
requirement satisfiable wherever an implementer happened to put it.

The count is **not** obtainable from `errors="replace"`: the decoder exposes
no counter, and counting `U+FFFD` in the output is wrong for sources that
legitimately contain it. Use a `codecs.register_error` handler with per-stream
counting inside the D1.8 wrapper — the error registry is global, so the
handler must be registered under a per-stream name or resolve its counter via
a ContextVar.

The run-level warning needs a **declared** `RunErrorSummary` field — name it
`decode_replacements`. This is the one real coupling with S2; see Sequencing.

**D1.5 — Attribute at the step, then re-raise.** *Revised.* The original
wording — "`step_executor.py:227` must handle `InputStorageError` so the
failure is attributed to the step rather than escaping as an unhandled
exception" — contradicts D1.2 and its own acceptance criterion. Once D1.2
lands the exception is no longer unhandled: `run_coordinator.py:730` catches
it, and that handler is the **only** thing that writes
`error_summary={"storage_error": ...}` (`:741`). A `step_executor` that
swallows it would defeat the AC requiring `storage_error` to be populated.

So: log with step attribution at `step_executor.py:227`, then **re-raise** so
the coordinator still terminates the run and records the summary.

**D1.6 — Fix `LocalInputStorage` identically.** Same sampling flaw, same fix.

*Revised — `open_text` is not the only entry point.*
`LocalInputStorage.preview_file` calls `detect_encoding` and `open(...)`
**directly** (`input_storage.py:541`, `:547`, `:573`), bypassing `open_text`
entirely. A fix confined to `open_text` leaves it crashing exactly as before.
D1.6 covers `preview_file` on both providers. (`list_entries` at `:487` is
already safe — it hardcodes `errors="replace"`.)

**D1.9 — Surface the detected encoding; the silent failure is the worse
one.** *New.* D1.1 is right that a mid-stream latin-1 re-read is unacceptable,
but the same root cause has a **silent** sibling that no other decision
covers. When the 64 KiB prefix contains a byte such as `0xE1`, UTF-8 is
rejected and `cp1252` selected for the whole stream. cp1252 rejects only five
byte values, so a genuinely UTF-8 file will usually decode **cleanly** as
cp1252 — and write mojibake into Salesforce with no exception, no warning and
no failed run. That is precisely D1.1's stated nightmare, arriving through the
front door rather than the error path.

Therefore: log the resolved encoding and whether it came from an override or
from a non-UTF-8 auto-detect fallback, and raise a **preflight warning** when
a non-UTF-8 encoding was auto-detected — e.g. "input decoded as cp1252
(auto-detected); set an explicit encoding to confirm". This gives the operator
a signal before the load writes anything, and routes them to D1.3.

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

`run_coordinator.py` writes **six** distinct keys into
`LoadRun.error_summary`. *Revision 2 — the original count of five missed
`unknown_exit`, which is written across two lines and so escapes a
single-line grep. Its omission is itself an instance of the drift this
defect describes.*

| Key | Write sites | Declared on `RunErrorSummary`? |
|---|---|---|
| `auth_error` | `:169`, `:665` | ✅ |
| `output_storage_error` | `:182`, `:652` | ❌ |
| `preflight_warnings` | `:615` (merge) | ✅ |
| `storage_error` | `:741` | ✅ |
| `unexpected_exception` | `:778` | ❌ |
| `circuit_breaker` | `:884` (merge) | ✅ |
| `unknown_exit` | `:1053` (merge) | ❌ |

`unknown_exit` is written by the SFBL-112 `finally` backstop — the
**last-resort** failure path, whose message an operator most needs and which
is currently guaranteed to be invisible.

No writers exist outside `run_coordinator.py`; `orchestrator.py:221-228` is a
delegating shim.

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

**D2.1 — Declare the missing fields.** *Revised — three, not two.* Add
`unexpected_exception`, `output_storage_error` and `unknown_exit`, all
`Optional[str]`, to `RunErrorSummary`. Plus `decode_replacements` from D1.4.

**D2.2 — Prevent recurrence with a contract test.** Adding fields fixes
today's drift but not the class of bug. This is the substantive fix; D2.1
alone will drift again.

*Revised — use the choke point, not an AST scan.* The original design
proposed statically collecting keys written in `run_coordinator.py`. That
works for today's code, where all seven sites use string-literal keys, but it
**false-passes** on the two pass-through paths that forward a caller-supplied
dict: `_mark_run_failed(..., error_summary=<variable>)` (`:1003`, `:1017`)
and `_mark_run_failed_safe` (`:1072`, `:1081`). A dynamically-built dict is
invisible to AST.

Every write — including the `error_summary=` kwarg path, which delegates at
`:1017-1018` — funnels through `_merge_run_error_summary` (`:980`). So the
primary mechanism is a test-scoped wrapper around that single function
asserting `set(updates) <= set(RunErrorSummary.model_fields)`. Roughly ten
lines, catches literal *and* dynamic keys, no AST.

Because that only fires on paths a test actually exercises, pair it with a
cheap static scan of `run_coordinator.py` for literal keys as the secondary
half. Both are cheap; neither is a harness.

**D2.3 — Surface it in the UI.** The run detail page must render whichever
error keys are populated. A run in `failed` state with no visible reason is
the failure mode being corrected.

*Revised — three consumers hardcode the key list, not one.*

1. `frontend/src/pages/RunDetail/RunSummaryCard.tsx:140` —
   `run.error_summary?.auth_error ?? run.error_summary?.storage_error`, with
   a generic "Run failed. See logs for details." fallback at `:146`. That
   fallback is exactly what the operator saw during the incident.
2. `frontend/src/api/types.ts:373-378` — `RunErrorSummary` duplicates the
   field list in TypeScript and must gain the same fields, or the new keys
   are unreachable under `strict`.
3. `mcp-server/src/sf_bulk_loader_mcp/tools/runs.py:200` — `format_run`
   iterates a hardcoded `("auth_error", "storage_error", "circuit_breaker")`.
   This is a **third independent instance of the same bug class**: fixing the
   Pydantic schema alone leaves the MCP surface blind.

`preflight_warnings` must be excluded from any generic renderer — it is a
`PreflightWarning[]`, already rendered by a dedicated block at
`RunSummaryCard.tsx:156-175`, and a naive `Object.entries` pass would emit
`[object Object]` and duplicate that banner. Iterate string-valued keys only.

`frontend/src/__tests__/pages/RunDetail.test.tsx:397-441` currently asserts
the generic fallback for an unmapped key. That assertion is superseded and
must be updated, not left to fail.

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
with `min_length=1` after whitespace trimming. `None` on update stays valid
(partial-update semantics); `""` does not.

*Revised — two errors in the original wording, both of which would have
produced a broken implementation.*

**(a) Constrain the input schemas only — never `LoadStepBase`.**
`LoadStepResponse` **inherits** `LoadStepBase` (`schemas/load_step.py:166`),
and `LoadPlanResponse.load_steps` is a `List[LoadStepResponse]`
(`schemas/load_plan.py:44`). Putting `min_length=1` on the base therefore
applies it to the **response** path, and Pydantic raises on `model_validate`
of the existing empty row — so `GET /api/plans/{id}` (`api/load_plans.py:103`),
plan duplication, step reorder and step update all return 500
`ResponseValidationError` for the incident plan.

That directly defeats D3.2: the plan editor cannot surface a validation error
against a row it can never fetch. Constrain `LoadStepCreate` and
`LoadStepUpdate`, or keep `LoadStepResponse.object_name` explicitly
unconstrained.

**(b) Do not reuse `_normalize_name`.** That helper
(`schemas/load_step.py:9-18`) maps empty/whitespace to **`None`**, which is
correct for the nullable `name` field and its partial unique index, and wrong
here. Mirrored onto `LoadStepUpdate.object_name`, `""` becomes `None`,
survives `model_dump(exclude_unset=True)` (`api/load_steps.py:151`) and writes
NULL into a `nullable=False` column (`models/load_step.py:41`) — a 500, not
the required 422. Write a distinct validator that strips whitespace and
returns the trimmed string, combined with `min_length=1`.

**D3.1a — Validate merged effective state on update.** *New.*
`api/load_steps.py:156-197` deliberately validates *merged* state for
`operation`, `soql`, `csv_file_pattern` and `input_from_step_id` "so partial
updates cannot produce invalid combinations". `object_name` gets no such
treatment, so a PATCH that simply omits it on an empty-`object_name` row
returns 200 and leaves `""` persisted — while every acceptance criterion still
passes. Add an `effective_object_name` check following the existing pattern.

**D3.2 — Do not migrate silently.** Existing rows with an empty
`object_name` must not be auto-populated or deleted. Surface them as a
validation error in the plan editor so the operator supplies the correct
object. A one-line startup log naming affected step IDs is sufficient; the
only hook is the `lifespan` at `app/main.py:53`.

*Note — `duplicate_plan` bypasses Pydantic entirely.*
`services/load_plan_service.py:81-86` clones steps via `_copy_columns`
straight into the ORM, so an existing empty `object_name` propagates into a
brand-new plan unvalidated. It is not a new-data source — it only replicates
existing bad rows — but it is the one creation path a schema constraint
cannot reach. Either reject duplication of an invalid plan with a clear 422,
or accept and document. (`api/load_steps.py:122` is fine: `step_data` is
schema-validated. MCP `add_step`/`update_step` go over HTTP and inherit
backend validation; their JSON schemas lack `minLength`, which only means a
422 instead of a client-side reject.)

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
- **`_s3_outcome_code`** (`input_storage.py:376`) maps a botocore
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
- Update the storage-flow section of `docs/observability.md` (≈ `:124-135`),
  which `_s3_outcome_code` cites as its reference. **Also `observability.md:74`**,
  which currently documents the preflight mapping as "`storage_error` for
  `InputStorageError`, `unexpected_exception` otherwise" — precisely the
  statement D4.2 and D1.2a invalidate.
- D4.1/D4.2 were confirmed sound by the QA pass and fit existing conventions:
  `OutcomeCode` (`events.py:289`) is a flat class of documented string
  constants, and `output_upload_error` (`:314`) — "S3 output upload failure
  (distinct from input `storage_error`)" — is direct precedent for splitting a
  code off `storage_error`. The three existing `INPUT_FAILED` sites (`:777`,
  `:884`, `:918`) each pair the event with a varying `outcome_code`, which is
  exactly the axis D4.2 describes.
- The `run_id`/`step_id` log-site requirement is satisfiable from
  `input_storage.py` even though it holds no run context: `logging_config`
  injects both from ContextVars into every record
  (`observability/context.py:45-46`, `logging_config.py:135-136`), and
  `step_executor` binds them before calling the inner implementation.

---

## Documentation impact

Per the Epic DoD, shipping this requires:

- `docs/usage/` — document the encoding override and `on_decode_error`
  policy on the relevant input-connection topic, with frontmatter
  `required_permission` checked against `backend/app/auth/permissions.py`.
- `docs/architecture/` — update any description of input storage decoding.
- `docs/specs/implemented/input-storage-spec.md` — add a banner noting this
  spec supersedes its encoding-detection section.
- `docs/ui-conventions.md` — update if any shared component or form pattern
  changes (S1 adds connection/step form controls; S2 changes the run error
  banner).
- README and stale-reference sweep, per the Epic DoD in `CLAUDE.md`.
- Run `node frontend/scripts/check-help-links.mjs` before pushing.

### E2E spec coverage

Per the *Epic DoD — E2E spec coverage* rule, this epic changes user-facing UI
flows and therefore ships Tier 1a coverage under
`tests/e2e/app/playwright/tier-1a/`:

- Run-detail error-summary rendering (S2).
- Plan-editor validation error on an empty `object_name` (S3).

Both are org-free, so Tier 1a is the correct tier.

---

## Story breakdown

**Three stories under one bug-fix epic, shipping as a single bundled PR**
(confirmed 2026-08-17). S1 is the only one that unblocks the failing load.

| Story | Ticket | Scope |
|---|---|---|
| S1 — Stream-safe input decoding | SFBL-401 | D1.1–D1.9 + D4.1–D4.2: `_DecodingTextStream` wrapper, `InputDecodeError` subclass with handler branching, file-absolute offsets, encoding + `on_decode_error` resolution chain (step → connection → app setting), re-raise at `step_executor.py:227`, fix `LocalInputStorage` incl. `preview_file`, all six `open_text` consumers, `INPUT_DECODE_ERROR` outcome code reusing `StorageEvent.INPUT_FAILED`, migration, API/frontend/MCP surface, tests |
| S2 — Run error visibility | SFBL-402 | D2.1–D2.3: add the three missing `RunErrorSummary` fields, `_merge_run_error_summary` choke-point contract test, render populated keys across all three consumers (`RunSummaryCard.tsx`, `types.ts`, MCP `tools/runs.py`) |
| S3 — Reject empty `object_name` | SFBL-403 | D3.1–D3.2 + D3.1a: constrain the *input* schemas only, distinct trim validator (not `_normalize_name`), merged-effective-state check on update, plan-editor validation error, tests |

### Sequencing

The stories are *mostly* independent, with **one real coupling**: D1.4's
replaced-character warning (`decode_replacements`) adds a field to
`RunErrorSummary` — the same class S2 rewrites, and its new key must satisfy
S2's contract test. Land S2's schema change first, or have S1's key land
alongside S2's field declarations. Otherwise the file-level split is clean:
S1 owns `input_storage.py`/`step_executor.py`, S3 owns `load_step.py`.

### Acceptance criteria

**S1**
- A file whose first 64 KiB decodes cleanly but which contains an invalid
  byte later fails with `InputDecodeError`, not `UnicodeDecodeError`.
  *Falsification: if the prefix-sampling fix were reverted, this test must
  fail.*
- The error message names the file, the encoding attempted, the byte value,
  and a **file-absolute** byte offset. A test plants a bad byte at a known
  offset well beyond the 64 KiB window and asserts the reported offset equals
  that offset exactly — not a chunk-relative one. *Falsification: the
  pre-fix code reports a chunk-relative offset, which must fail this.*
- The run terminates with `storage_error` populated in `error_summary`, and
  is observable via the run detail endpoint **without S2 merged**.
- The failure logs with `event_name=storage.input.failed` and
  `outcome_code=input_decode_error` — **not** `storage_error` — from *both*
  the preflight handler and the fatal handler. No new `StorageEvent` member
  is added.
- All six `open_text` consumers are covered. Specifically, step preview
  (`api/load_steps.py:438`) surfaces a decode failure to the operator; a test
  asserts it does **not** report `row_count=0` for a corrupt file.
  *Falsification: the naive `except InputStorageError` swallow must fail this.*
- `step_executor` logs the failure with step attribution **and re-raises**; a
  test asserts the run still terminates with `storage_error` populated.
- Encoding resolution order is step → connection → app setting →
  auto-detect, with a test per tier. An invalid encoding name is rejected at
  write time, not at read time.
- `on_decode_error=replace` completes the load and records an accurate
  replaced-character count. A test uses a file with a known number of bad
  bytes and asserts the exact count. *Falsification: counting `U+FFFD` in the
  output must fail on a source that legitimately contains `U+FFFD`.*
- A non-UTF-8 auto-detected encoding raises a preflight warning naming the
  encoding (D1.9). *Falsification: a genuinely UTF-8 file whose prefix forces
  a cp1252 guess must produce this warning rather than loading silently.*
- Equivalent coverage for `LocalInputStorage`, including `preview_file`,
  which does not route through `open_text`.
- `detect_encoding` no longer reads the whole file into memory; a test asserts
  the bytes read are bounded by the sample size for a file much larger than
  the detection window.
- A bad byte planted inside a record with recognisable field content produces
  an error message that does **not** contain that content (`sanitization.py`).
- The operator can set both the encoding override and the decode-error policy
  from the connection form and the step editor, and the values round-trip
  through `GET`. *Falsification: a backend-only implementation must fail this.*

**S2**
- A run failing via the broad step-loop handler exposes
  `unexpected_exception` through the run detail endpoint. Equivalent coverage
  for `output_storage_error` and `unknown_exit`.
- The contract test fails if a new `error_summary` key is added without a
  matching `RunErrorSummary` field. Prove it by introducing a throwaway key
  in the test and asserting the check flags it — *a test that only passes
  against today's code does not satisfy this criterion.*
- The contract test catches a **dynamically built** dict passed through
  `_mark_run_failed(error_summary=...)`, not only string literals.
  *Falsification: a pure AST scan must fail this case.*
- All three consumers render the new keys: the run detail page, the
  TypeScript `RunErrorSummary` type, and MCP `format_run`.
- `preflight_warnings` still renders through its dedicated block and is not
  stringified by the generic renderer.

**S3**
- `POST`/`PUT` of a load step with `object_name` of `""` or whitespace-only
  returns 422. *Falsification: if the trim were dropped and only
  `min_length=1` applied, the whitespace cases must still fail.*
- `object_name: None` on update remains valid (partial update semantics).
- `GET /api/plans/{id}` returns **200** for a plan containing a step whose
  stored `object_name` is `""`. *Falsification: constraining `LoadStepBase`
  makes this 500 — this test is what catches it.*
- A partial update that **omits** `object_name` on a step whose stored value
  is empty returns 422. *Falsification: without the merged-state check this
  returns 200 and the bad row survives while all other criteria pass.*
- Existing rows with an empty `object_name` surface a validation error in the
  plan editor and are not silently rewritten by any migration.

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
