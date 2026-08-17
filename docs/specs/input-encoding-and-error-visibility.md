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

**Revision 3 (2026-08-17)** — the actual `Account_sample.csv` was obtained
and analysed. The production error reproduces byte-for-byte. The headline
finding is that **the file is mixed-encoding and no single codec decodes
it**, which the earlier revisions assumed away. See
[Ground truth](#ground-truth--analysis-of-the-actual-file).

**Revision 4 (2026-08-17) — scope cut.** The destination org (`ucas-mig2`)
was inspected to see what the *official Salesforce Data Loader* did with the
same file. It read the file as **windows-1252** and **silently corrupted 25
Account records** — it never failed. See
[What the official Data Loader did](#what-the-official-data-loader-did).

That reframes the problem and **reverses two decisions taken in revision 3**:

- **The file is malformed.** Mixed encoding is not a legitimate input format.
  The product's job is to *refuse it legibly*, not to load it.
- **D1.4 (`on_decode_error=replace`) is CUT.** Revision 3 declared it
  "load-bearing" because it was the only in-product path that loads this
  file. That reasoning was wrong: the file should not load. D1.4 existed to
  make a malformed file load lossily when a **lossless** offline repair
  already exists, at the cost of the most intricate machinery in the epic
  (custom `codecs.register_error` handler, per-stream counting, ContextVars).
- **D1.3's app-setting tier is CUT.** Step-level override already reaches the
  default input source; the extra tier was solving a problem that the step
  override covers.
- **C5 / SFBL-404 (per-row quarantine) is parked**, for the same reason as
  D1.4: it is a mechanism for partially loading a malformed file.
- **D1.9 is strengthened.** It was written as a hypothetical. It is now a
  documented production incident, caused by the official tool.

Net effect: the two most complex pieces of the epic are removed, and what
remains does one job — never silently corrupt, and say precisely what is
wrong and where.

**Revision 5 (2026-08-17) — remove encoding auto-detection entirely.**
*Owner decision.* Revisions 1–4 all took prefix-sampled auto-detection as a
given and built machinery to manage its failure modes. Revision 5 removes the
guess instead.

**Input is decoded as UTF-8 unless an explicit override says otherwise.**

Rationale: detecting an encoding from a 64 KiB prefix and applying it to a
whole stream is unsound *by construction*, and no amount of error handling
makes it sound. Every prior revision was adding machinery around that
unsoundness — better failures (D1.2, D1.8), a warning when the guess looks
suspicious (D1.9), an escape hatch when it is wrong (D1.3). Removing the
guess deletes the failure class rather than managing it.

Consequences:

- **D1.0 (new)** — UTF-8 is the default and the candidate ladder is deleted.
- **D1.3 is promoted** from optional escape hatch to the *primary* mechanism.
- **D1.9 is CUT** — there is no auto-detected encoding left to warn about.
  The silent-mojibake class it existed to catch cannot occur.
- **D1.7 is resolved by deletion** — `detect_encoding` and
  `detect_encoding_from_bytes` are removed outright, so the whole-file
  `read_bytes()` defect goes with them.
- **D1.10 (new)** — on decode failure, run a *diagnostic* pass and tell the
  operator what the file looks like. Diagnose, never act.
- This is a **behaviour change** for existing users; see
  [Rollout](#rollout--this-is-a-behaviour-change).

Note this is a net **deletion** of code, not an addition.

**Revision 6 (2026-08-17) — encoding is a step-level setting only.**
*Owner decision.* D1.3 had specified the override on **both** the input
connection and the step. The connection-level field is removed; there is one
field, on the step.

The existing data model already draws this line, and connection-level
encoding was the first thing to cross it:

- `InputConnection` carries **transport only** — `provider`, `bucket`,
  `root_prefix`, `region`, credentials, `direction`. It holds no data-format
  settings whatsoever: no delimiter, no header configuration, nothing about
  how to interpret bytes.
- `LoadStep` carries **every** data-shape setting — `object_name`,
  `operation`, `external_id_field`, `csv_file_pattern`, `soql`,
  `partition_size`, `assignment_rule_id`.

Encoding is a data-format property, so it belongs on the step. `partition_size`
is the precedent for its exact shape: optional on the step, falling back to a
default — *not* to a connection field.

Two further points that were already true and should have settled this
earlier:

- `LoadStep.input_connection_id` is **nullable**. A step using the default
  input source has no connection row, so a connection-level field is
  unreachable for it. Step-level was always mandatory; connection-level was
  always merely additive.
- Two places to set one value means a resolution order to document and test,
  two schema fields, two migration columns, two form controls, extra MCP tool
  parameters, and a predictable support class — *"I set it on the connection,
  why isn't it applying?"*

*Provenance, for the record:* connection-level came from revision 2's original
D1.3 phrasing and survived four revisions unchallenged, including the
revision-4/5 scope review that questioned whether D1.3 was worth building but
never asked where it belonged.

**No global default setting either.** A deployment-wide encoding default was
considered and rejected: the step-level override covers every case
functionally, and `settings_service` remains available if a real need appears.
Building it speculatively is the over-engineering this epic has been trimming.

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

### Ground truth — analysis of the actual file

**Revision 3 (2026-08-17).** The offending `Account_sample.csv` (696,790
bytes — the exact size cited above) was obtained and analysed directly. The
production error reproduces **byte-for-byte**:

```
'charmap' codec can't decode byte 0x81 in position 6362: character maps to <undefined>
```

This settles the chunk-relative offset question empirically: the true file
offset of that `0x81` is **219,354**. The reported `6362` is off by a factor
of 34, and sits inside the 64 KiB window that was never the problem.

**The file is mixed-encoding. No single encoding decodes it.**

This is the finding that changes the design. The file is not "a cp1252 file"
or "a UTF-8 file with a corrupt byte" — it contains *both* encodings
interleaved:

| Codec | Result on the real file |
|---|---|
| `utf-8` | **Fails** — 38 separate invalid sequences (`0xe1 0xe3 0xe9 0xe4 0xeb 0xef 0xf3 0xf4 0xfd`, i.e. single-byte latin-1 `á ã é ä ë ï ó ô ý`) |
| `cp1252` | **Fails** — 3 undefined bytes, at offsets 219,354 (`0x81`), 512,085 (`0x8D`), 534,793 (`0x90`) |
| `latin-1` | Never raises, but **silently mojibakes 25 rows** |

The three cp1252-undefined bytes are not corruption — they are *legitimate
UTF-8 continuation bytes*. Decoded as UTF-8 the affected fields read
`Áhufglq-wmunh`, `Ezpnáčegfá`, `Vbươeg Đeàb` — Vietnamese and Czech names.
Meanwhile the 38 UTF-8 failures are genuine single-byte latin-1 accents. Both
encodings are present in the same file, in the same column.

Scale: **7,684 rows, of which only 60 contain any non-ASCII byte at all.**
39 valid UTF-8 multi-byte sequences; 38 single-byte latin-1 characters.

Rows altered, by strategy:

| Strategy | Rows altered (of 7,684) |
|---|---|
| `cp1252` + `replace` | **3** |
| `latin-1` (silent) | 25 mojibaked, no error |
| `utf-8` + `replace` | 36 |

### What the official Data Loader did

The destination sandbox `ucas-mig2` was inspected, because the same file was
previously loaded there with the **official Salesforce Data Loader**. The
result is the most important evidence in this document.

**It read the file as windows-1252, and silently corrupted 25 Accounts.**

All 60 non-ASCII rows from the file were matched to their Account records by
`UCAS_Account_Id__c` and compared:

| Hypothesis | Rows matching (of 60) |
|---|---|
| Stored value equals a **windows-1252** decoding of the file | **60** |
| Stored value equals a **UTF-8** decoding of the file | **0** |

The discriminator is the `0x80`–`0x9F` range, where cp1252 and latin-1
diverge. The org holds `0x82`→`‚` (U+201A), `0x99`→`™` (U+2122),
`0x9A`→`š` (U+0161), `0x9E`→`ž` (U+017E) — cp1252 mappings specifically, not
latin-1. And the five bytes cp1252 leaves *undefined* — the very bytes that
crash our loader — are stored as raw C1 control characters (`U+0081`,
`U+008D`, `U+0090`), because Java's `windows-1252` decoder passes them
through where Python's raises.

windows-1252 is the JVM platform default charset on Windows, so this is
almost certainly an unconfigured Data Loader rather than a deliberate choice.

Damage: **25 of 7,684 Accounts** hold mojibake. `Áhufglq-wmunh` is stored as
`Ã` + `U+0081`; `Vbươeg Đeàb` as `VbÆ°Æ¡eg Ä` + `U+0090` + `eÃ `. The
Vietnamese, Czech, Polish and Turkish names are destroyed, and three records
contain unprintable control characters. The remaining 35 non-ASCII rows —
those carrying only single-byte cp1252 accents — loaded correctly.

**The load reported success.** Nothing failed, nothing warned, and the
corruption is invisible to anyone not specifically looking for it.

### Consequences for the design

**C1 — the file is malformed, and the product's job is to refuse it.** Mixed
encoding is not a legitimate input format. No feature should be built to
consume it as though it were. The design already reflects this: pick one
encoding, fail loudly when it does not hold, let the operator override it.
That is refusing malformed input legibly — not accommodating it.

**C2 (revised — reverses revision 3) — D1.4 is cut.** Revision 3 declared
`on_decode_error=replace` "load-bearing" on the grounds that it was the only
in-product path that loads this file. That reasoning was wrong: **this file
should not load.** Refusing it with an actionable error *is* the correct
behaviour, and the operator then repairs it losslessly. D1.4 would have
bought a lossy load of a malformed file at the price of the most intricate
machinery in the epic, when a lossless offline repair already exists.

**C3 (revised) — there is no "recommended encoding" for this file.** Both
`cp1252` and `utf-8` are wrong for part of it. The remedy is to repair the
file, not to choose better. The error message should name the byte and offset
so the operator can find it; it should not recommend an encoding, because any
recommendation would be wrong for some of the data.

**C4 (strengthened) — D1.9 is no longer hypothetical.** It was written as a
warning about a silent failure mode. That failure mode has now **happened in
production, caused by the official tool**, and is sitting in `ucas-mig2`. Our
own code would do the same whenever the offending bytes fall outside cp1252's
five undefined values — the loud crash we are fixing is the *lucky* outcome;
the silent success is the dangerous one. D1.9 is the decision that addresses
the dangerous case, and it is now the highest-value item in the epic after
the crash fix itself.

**C5 (revised) — per-row quarantine is parked.** It is a mechanism for
partially loading a malformed file, and falls to the same objection as D1.4.
SFBL-404 remains filed as an idea with independent merit for genuinely bad
individual rows, but it is **not** part of this epic and should not be built
on the strength of this incident.

**C6 — the operator workaround in revisions 1–2 was wrong.** `iconv -f UTF-8
-t UTF-8` cannot re-encode a mixed file; there is no single source encoding to
convert *from*. Corrected below.

**C7 — the real bug is upstream, in the extract/obfuscation pipeline.**
Whatever generated this file emitted UTF-8 and cp1252 into the same column,
and will keep doing so until fixed. No amount of loader tolerance addresses
it.

The file is production data passed through an obfuscation process that
rewrites account IDs and names. Four findings locate the defect in that
pipeline rather than in the production source:

1. **The same character is written both ways.** `á` appears as UTF-8 twice
   and as cp1252 nine times — same column, same file. No single consistent
   producer does that.
2. **Encoding was chosen per record.** 50 of the 60 non-ASCII rows fit
   *"write cp1252 if the value fits, else fall back to UTF-8"* — a per-value
   encoder fallback, which is a **writer** behaviour, not a property of
   stored data. (The 10 exceptions all involve `í`, written as UTF-8 even
   though cp1252 could represent it; unexplained, and worth noting rather
   than explaining away.)
3. **The two encodings are uniformly interleaved** across all 7,684 rows —
   UTF-8 rows average position 3,828, cp1252 rows 3,425, against a uniform
   expectation of 3,842. Not a concatenation of two files, and not a
   merge-and-sort.
4. **It is a single extract.** `RecordTypeId` and `OwnerId` each hold exactly
   one distinct value across every row. There is no second source to blame.

Salesforce stores text as Unicode and its APIs return UTF-8; it cannot emit
per-row varying encodings. So the mixing was introduced downstream of
Salesforce, by whatever wrote this CSV.

*Limit of the evidence:* the corruption is confined to `Name`, the column the
obfuscation rewrites — but the ID columns are alphanumeric and would carry no
encoding signal either way, so that confinement is weaker evidence than it
looks. The findings establish that *a writer* chose encodings per value; they
do not separate the obfuscation step from the export step feeding it. The
settling experiment is to run known-clean UTF-8 input containing both
cp1252-representable accents (`í á é`) and non-representable ones
(`ł ę ğ č ư Đ`) through the obfuscation and strict-decode the output.

This is context for the epic, not work in it — but it is why C1 holds: the
product should refuse malformed input and let it be fixed at source.

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

**D1.0 — UTF-8 by default; no auto-detection.** *New at revision 5, and the
decision the rest of this section now hangs off.*

Delete `_ENCODING_CANDIDATES`, `detect_encoding` and
`detect_encoding_from_bytes`. Input is decoded as UTF-8 unless an explicit
override (D1.3) says otherwise. A file that is not UTF-8 and not overridden
fails with an actionable error (D1.2) plus a diagnostic (D1.10).

**The default codec must be `utf-8-sig`, not bare `utf-8`.** This matters more
than it looks: Excel on Windows writes UTF-8 *with* a BOM by default, and
under bare `utf-8` the BOM survives into the first header field as `﻿Name`
instead of `Name`. Header matching then fails on the first column — silently,
because the value still looks fine in most displays. `utf-8-sig` reads BOM and
non-BOM UTF-8 identically, so it is strictly the better default. Verified:

| File | `utf-8` | `utf-8-sig` |
|---|---|---|
| With BOM | `'﻿Name'` ❌ | `'Name'` ✅ |
| Without BOM | `'Name'` ✅ | `'Name'` ✅ |

Present it in the UI as "UTF-8"; `utf-8-sig` is the implementation detail.

Call sites to change when detection is removed:

- `input_storage.py:541` (`preview_file`), `:662` (`open_text`), `:929` (S3
  `open_text`) — use the resolved encoding.
- `csv_processor.py:124`, `:204` — already `encoding or detect_encoding(...)`;
  the fallback becomes the default constant.
- `csv_processor.py:321`, `:341` — these detect on **Salesforce result files**
  (downloaded error/unprocessed CSVs), which are always UTF-8. Replace with a
  hard `utf-8` decode; detection there was never meaningful.
- Delete the ~9 `detect_encoding` tests in `test_csv_processor.py` and
  `test_input_storage.py` and replace with override-resolution tests.

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

**D1.3 — Explicit encoding override, on the step. PRIMARY MECHANISM.** Add a
single optional `encoding` field to **`LoadStep`**. With detection gone this
is no longer an escape hatch — it is the only way to read a non-UTF-8 file,
so it must be genuinely usable rather than an expert-only setting.

*Revision 6: step-level only.* The connection-level field specified in
revisions 2–5 is removed — `InputConnection` carries transport only, and
`LoadStep` already owns every data-format setting. See the revision-6 note
above.

*UI:* a **dropdown, defaulting to UTF-8**, in the step editor. A curated
allow-list, **not** free text and not the full ~100-codec Python list:

| Label | Codec |
|---|---|
| UTF-8 *(default)* | `utf-8-sig` |
| Windows-1252 | `cp1252` |
| ISO-8859-1 (Latin-1) | `latin-1` |
| UTF-16 | `utf-16` |

The field is optional and renders as "UTF-8 (default)" when unset, so the
override reads as a deliberate choice rather than a required decision. The
allow-list is validated server-side — a value outside it is rejected at write
time, not at read time.

Keep the list short. Every entry is a way for an operator to mis-set the
encoding and corrupt data, so entries need to earn their place; add more only
on evidence of a real source that needs them.

**D1.10 — Diagnose on failure; never act on the diagnosis.** *New at revision
5.* Removing detection means a previously-working cp1252 file now fails. That
is the intended trade — visible failure over silent corruption — but the
failure has to be genuinely actionable, or we have just moved the operator's
problem rather than solved it.

So on decode failure only (the run is already dead; a second read costs
nothing that matters), re-read the file and report **what it looks like**,
without using the answer:

- If the whole file decodes cleanly as one of the allow-listed codecs:
  *"`Account_sample.csv` is not valid UTF-8 (byte `0x81` at offset 219354).
  The file decodes cleanly as Windows-1252 — if that is correct, set Encoding
  on the step."*
- If **no** allow-listed codec decodes the whole file:
  *"`Account_sample.csv` is not valid UTF-8 (byte `0x81` at offset 219354),
  and no supported encoding decodes the whole file. It appears to contain
  mixed encodings and should be repaired at source."*

That second branch is exactly the incident case, and it tells the operator the
truth that took days to establish by hand. Crucially the product still refuses
the file — the guess is offered as *advice to a human*, never acted on. That
is the whole difference between this and the auto-detection being removed.

This supersedes C3, which said the message should not name an encoding. C3 was
right that the product must not *choose* one; naming a candidate as advice,
with the mixed-encoding case called out explicitly, does not reintroduce that
risk.

*Why step-level is the only workable home.* `get_storage`
(`input_storage.py:1008-1017`) resolves `None`, `""`, `"local"` and
`"local-output"` to the default storage **without a connection record at
all**, so a connection-level field would be unreachable for every step using
the default input source. Step-level was always the mandatory half.

Resolution: step `encoding` if set, otherwise the **UTF-8 default**. One
field, one fallback, no precedence chain to document or test.

The encoding name must be validated at write time. Today an invalid value
would surface only at read time, because `detect_encoding_from_bytes` swallows
`LookupError` (`input_storage.py:115`).

**D1.4 — ~~Opt-in lossy mode~~ — CUT at revision 4.**

Revision 3 proposed an `on_decode_error` policy with a `replace` mode, and
declared it load-bearing because it was the only in-product path that loads
the incident file. **That was the wrong conclusion.** The file is malformed;
it should not load. See C1/C2.

Cut for three reasons:

1. It buys a **lossy** load of a malformed file when a **lossless** offline
   repair already exists — strictly worse for the operator.
2. It is the most intricate machinery in the epic. The replaced-character
   count is not obtainable from `errors="replace"` (the decoder exposes no
   counter, and counting `U+FFFD` is wrong for sources that legitimately
   contain it), so it needs a `codecs.register_error` handler with per-stream
   counting via a per-stream registration name or a ContextVar.
3. It was the only thing coupling S1 to S2, via a new `decode_replacements`
   field on `RunErrorSummary`. **Cutting it makes S1 and S2 fully
   independent.**

If a genuine lossy-source use case appears later, reopen it on that evidence
rather than on this incident.

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

**D1.9 — ~~Surface the detected encoding~~ — CUT at revision 5, because the
problem it solved no longer exists.**

D1.9 existed because auto-detection could pick cp1252 for a genuinely UTF-8
file (cp1252 rejects only five byte values, so the wrong guess usually decodes
*cleanly*) and write mojibake into Salesforce with no exception, no warning
and no failed run. That is not hypothetical — it is exactly what the official
Data Loader did to `ucas-mig2`, corrupting 25 Accounts while reporting
success.

**D1.0 removes the guess, so the failure mode cannot occur.** With UTF-8 as
the default, a UTF-8 file is read as UTF-8 — always. A non-UTF-8 file fails
loudly rather than being silently mis-decoded. There is no detected encoding
left to warn about.

This is the clearest illustration of why revision 5 is the better design:
revisions 2–4 spent an entire decision, a metric, a preflight-warning channel
and its acceptance criteria on *managing* a bad guess. Deleting the guess
removes all of it and leaves the system safer.

One element of D1.9 is worth keeping in a reduced form: **log the resolved
encoding and where it came from** (step override or UTF-8 default). That is
cheap, it makes "which encoding did we actually use for this run?" answerable
from the logs, and it carries none of the warning machinery D1.9 required.

**D1.7 — ~~Bound the local sample read~~ — RESOLVED BY DELETION at revision
5.** `detect_encoding` is removed outright by D1.0, taking the defect with it.
Retained below for the record.

*Original text.* Added 2026-08-17 during ticketing;
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
`Optional[str]`, to `RunErrorSummary`. (Revision 3 also required
`decode_replacements` from D1.4; **D1.4 is cut at revision 4**, so that field
is no longer needed and S2 has no dependency on S1.)

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
- Log the **resolved encoding and its source** (step override / UTF-8 default)
  on every input read, so "which encoding did this run use?" is answerable
  without guessing. (Supersedes revision 3's replaced-character metric, cut
  with D1.4 at revision 4, and revision 4's auto-detect metric, cut with D1.9
  at revision 5 — there is no detection left to measure.)
- Count decode failures by `outcome_code=input_decode_error`. After the
  rollout this doubles as the migration signal: a spike means users whose
  files were previously auto-detected now need the override set.
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

- `docs/usage/` — document the encoding dropdown on the **step / plan-editor**
  topic, with frontmatter `required_permission` checked against
  `backend/app/auth/permissions.py`. Must state plainly that **input is
  expected to be UTF-8**, that non-UTF-8 sources set Encoding on the step, and
  that **a mixed-encoding file cannot be fixed by any override** and must be
  repaired at source. The input-connection topic needs no change — encoding is
  not a connection setting.
- **Release notes — breaking change.** Auto-detection is removed; sources that
  relied on it must set Encoding on the step. State the remedy
  in the note itself, not just the change.
- `docs/architecture/` — update any description of input storage decoding.
  Revision 5 deletes the detection layer entirely, so anything describing the
  cp1252/latin-1 ladder is now wrong rather than merely stale.
- `docs/specs/implemented/input-storage-spec.md` — banner noting this spec
  supersedes its encoding-detection section. Note the *original* spec §4.3
  specified detection as a feature (commit `b85c755`), so this is a
  deliberate reversal of an original design decision and should be recorded
  as such rather than presented as a bug fix.
- `docs/ui-conventions.md` — update if any shared component or form pattern
  changes (S1 adds the step-editor encoding dropdown; S2 changes the run error
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
| S1 — UTF-8-by-default input decoding | SFBL-401 | D1.0–D1.3, D1.5, D1.6, D1.8, D1.10 + D4.1–D4.2 (**D1.4, D1.7, D1.9 cut/resolved**): delete auto-detection, `utf-8-sig` default, encoding dropdown (step-level only), `_DecodingTextStream` wrapper, `InputDecodeError` subclass with handler branching, file-absolute offsets, failure diagnostics, re-raise at `step_executor.py:227`, fix `LocalInputStorage` incl. `preview_file`, all six `open_text` consumers, `INPUT_DECODE_ERROR` outcome code reusing `StorageEvent.INPUT_FAILED`, migration, API/frontend/MCP surface, tests |
| S2 — Run error visibility | SFBL-402 | D2.1–D2.3: add the three missing `RunErrorSummary` fields, `_merge_run_error_summary` choke-point contract test, render populated keys across all three consumers (`RunSummaryCard.tsx`, `types.ts`, MCP `tools/runs.py`) |
| S3 — Reject empty `object_name` | SFBL-403 | D3.1–D3.2 + D3.1a: constrain the *input* schemas only, distinct trim validator (not `_normalize_name`), merged-effective-state check on update, plan-editor validation error, tests |

### Rollout — this is a behaviour change

Removing auto-detection means **a file that loads today may fail after this
ships**. Specifically: any source that is cp1252 or latin-1, on a step with
no explicit `encoding`, currently auto-detects and loads; afterwards it fails
until someone sets the dropdown on that step.

This is the intended trade, and the honest framing is that some of those loads
are *already* silently corrupting data — the Data Loader evidence shows what
that looks like, and by definition nobody has noticed. Converting invisible
corruption into a visible, one-dropdown-to-fix failure is the point of the
change, not a side effect of it.

**Decision: hard switch, no backfill.** Existing steps default to UTF-8 like
new ones. Rejected alternatives:

- *Backfill by detecting over recent files at migration time* — reintroduces
  the guess, and bakes it into stored config where it is harder to notice and
  harder to undo than the runtime version we are removing.
- *Grandfather existing steps onto a legacy auto-detect mode* — keeps the
  silent-corruption path alive indefinitely for exactly the steps most likely
  to be affected by it.

What makes the hard switch acceptable is D1.10: the failure names the file,
the byte, the offset, and what the file appears to be, so the fix is one
dropdown change rather than an investigation. Without D1.10 this rollout would
not be defensible.

Release notes must call this out explicitly as a breaking change, with the
remedy (set Encoding on the step) stated in the note itself.

### Sequencing

**Revision 4: the three stories are now fully independent.** The only
coupling was D1.4's `decode_replacements` field on `RunErrorSummary`, and
cutting D1.4 removes it. The file-level split is clean: S1 owns
`input_storage.py`/`step_executor.py`, S2 owns `load_run.py` plus the three
error-summary consumers, S3 owns `load_step.py`.

They still ship as **one bundled PR** — they share an incident and a
validation cycle, and S1+S2 are jointly needed for the fix to be observable.

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
- **`detect_encoding`, `detect_encoding_from_bytes` and `_ENCODING_CANDIDATES`
  no longer exist.** *Falsification: a grep for them in `backend/app` returns
  nothing. An implementation that keeps detection as a fallback must fail
  this.*
- A cp1252 file with **no** override fails rather than loading.
  *Falsification: this file loads successfully under the pre-change code, so
  a test asserting failure proves detection is genuinely gone.*
- The same file loads correctly **with** the step's `encoding` set to
  Windows-1252.
- A **UTF-8 file with a BOM** loads with its first header field intact —
  `Name`, not `﻿Name`. *Falsification: a bare `utf-8` default must fail
  this; only `utf-8-sig` passes.*
- The step override applies to files from the **default** input source,
  which has no `InputConnection` row. *Falsification: a connection-level
  design cannot satisfy this, which is why the field lives on the step.*
- There is **no** `encoding` field on `InputConnection`. *Falsification: a
  grep of the input-connection model, schemas, frontend types and MCP tools
  returns nothing.*
- An encoding value outside the allow-list is rejected at write time with a
  422, not at read time.
- **D1.10, the two diagnostic branches:** a file that decodes cleanly as
  cp1252 produces an error naming Windows-1252 as a candidate; the attached
  mixed-encoding `Account_sample.csv` produces an error stating that *no*
  supported encoding decodes the whole file and that it should be repaired at
  source. *Falsification: an implementation that silently retries with the
  diagnosed encoding, rather than only reporting it, must fail — the run must
  still terminate in both branches.*
- Equivalent coverage for `LocalInputStorage`, including `preview_file`,
  which does not route through `open_text`.
- (Revision 5: the former D1.7 criterion about bounded sample reads is
  obsolete — `detect_encoding` is deleted outright, so there is no sample
  read left to bound.)
- A bad byte planted inside a record with recognisable field content produces
  an error message that does **not** contain that content (`sanitization.py`).
- The operator can set both the encoding override and the decode-error policy
  from the step editor, and the value round-trips
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

**Corrected at revision 3 after analysing the real file.** The original
advice — "re-encode as UTF-8, locate bytes with `iconv -f UTF-8 -t UTF-8`" —
does not work. The file is mixed-encoding, so there is no single source
encoding to convert *from*, and `iconv` will simply abort at the first of 38
invalid sequences without repairing anything.

The file must be repaired **per byte-run**, not converted wholesale: decode
as UTF-8 where the bytes form valid sequences, and as cp1252 where they do
not. There is no one-line `iconv` equivalent.

The script below was **run against the real file and verified**: output is
7,684 rows of clean UTF-8, zero replacement characters, all 77 accented
characters recovered correctly across both classes (`é í á ł ã ı ğ ï ë ę ä ń`
plus the Vietnamese and Czech sequences). **The repair is lossless** — no
data is sacrificed, which makes it strictly better than any in-product
decoding strategy for this file.

```bash
python3 - <<'PY'
raw = open('Account_sample.csv','rb').read()
out, i = bytearray(), 0
while i < len(raw):
    for n in (4,3,2):                     # longest valid UTF-8 run wins
        if i+n <= len(raw):
            try:
                raw[i:i+n].decode('utf-8'); out += raw[i:i+n]; i += n; break
            except UnicodeDecodeError:
                pass
    else:                                  # not UTF-8 — treat as cp1252
        out += raw[i:i+1].decode('cp1252', errors='replace').encode('utf-8')
        i += 1
open('Account_sample.utf8.csv','wb').write(out)
PY
```

Re-upload the repaired file to
`s3://ucas-ani-prod-infrastructure-salesforce/test_inputs/`.

To inspect the damage before repairing, these list the two failure classes
separately:

```bash
python3 -c "raw=open('Account_sample.csv','rb').read(); print([hex(b) for i,b in enumerate(raw) if b in {0x81,0x8D,0x8F,0x90,0x9D}])"
```

The names are obfuscated/generated, but the accented characters are
**genuine** — Vietnamese (`ư ơ Đ`), Czech (`č`), Polish (`ł ę ń`) and Turkish
(`ı ğ`) among them — not generator artefacts. A latin-1 round-trip would
*not* preserve them; it mojibakes 25 rows.

**There is no in-app equivalent, by design.** Revision 3 proposed
`encoding=cp1252` + `on_decode_error=replace` as a get-going-now option;
revision 4 cut it. A malformed file is repaired at source, losslessly, using
the script above — the product's job is to tell the operator precisely which
byte is wrong and where, not to load it anyway.

Note also that the destination org already contains 25 Accounts corrupted by
the previous Data Loader run of this file. Repairing the CSV and re-upserting
on `UCAS_Account_Id__c` fixes both the file and the existing damage.
