# `Implement/week6` vs `main` — Implementation Summary

This document covers everything implemented on this branch (`Implement/week6`)
relative to `main`: 10 commits, Week 4 → Week 6, spanning hybrid retrieval,
document ingestion quality, an evaluation/regression harness, an LLM-judge
system, PII redaction, tracing/replay tooling, and two root-caused
retrieval/chunking bug fixes.

Commit range: `43d3c23` (Week 4) → `e9cdf4f` (Week 6, latest).

---

## 1. Features implemented

### 1.1 Hybrid retrieval (Week 4) — `app/services/retrieval_service.py`
Retrieval was pure vector similarity before this branch. Added:
- **BM25 keyword search** (`rank-bm25`) run alongside the existing ChromaDB
  vector search.
- **Reciprocal Rank Fusion (RRF)** (`_rrf_fusion`) to merge the two ranked
  lists into one candidate set, instead of trusting vector similarity alone.
- **Cross-encoder reranking** (`cross-encoder/ms-marco-MiniLM-L-6-v2`, via
  `sentence-transformers`) over the fused candidates, with a keyword-overlap
  fallback if the model fails to load.
- **`_apply_scenario_mismatch_penalty`** (Week 6): demotes a chunk whose text
  is gated on a specific scenario (fraud / misrepresentation / non-disclosure
  / policy voided / forfeiture) unless the question itself names that
  scenario — fixes the cross-encoder ranking a forfeiture clause #1 on
  "premium"/"claim" keyword overlap alone. See §2.

### 1.2 Guardrail simplification (Week 4) — `app/services/guardrail_service.py`
Removed the keyword-list short-circuit and the extra LLM call
(`_llm_check`) that classified "is this insurance-related" before every
answer. Guardrail now only checks for prompt-injection and forbidden-action
patterns; scope enforcement is left to the answer-grounding rules in the LLM
prompt itself. Net effect: one fewer LLM round-trip per request.

### 1.3 Document ingestion quality (Week 5–6) — `app/services/pdf_loader.py`, `app/services/chunk_service.py`
- **`pdf_loader.py`**: unicode normalization, mojibake-rupee-symbol repair,
  currency normalization, fragmented-URL repair, repeated header/footer line
  detection and stripping (per-document, not hardcoded), and **table
  extraction** merged inline with page text (`_extract_tables`,
  `_merge_text_and_tables`) so table rows aren't lost or duplicated against
  the surrounding prose.
- **`chunk_service.py`**: heading/table/key-value-aware line classification
  and logical-unit grouping, replacing a flat character-window splitter.
  Week 6 added **`_split_oversized_unit`**, which splits an over-limit
  logical unit only at sentence boundaries — see the bug fix in §2.
- **`ingestion_pipeline.py`** *(new)*: extracted the PDF → chunk → embed →
  store sequence out of `app/api/ingest.py` into one shared function
  (`run_ingestion_pipeline`), also used by `eval/run_eval.py`, so the API
  route and the offline eval script can't drift out of sync.

### 1.4 Embedding validation — `app/services/embedding_service.py`
Added `_validate_embedding` to check embedding dimensionality before storing,
so a malformed embedding fails ingestion loudly instead of silently
corrupting the vector store.

### 1.5 PII redaction for traces — `app/core/redaction.py` *(new)*
Regex-based redaction of policy/claim-number-shaped IDs
(`LETTERS-LETTERS-YYYY-ALPHANUM`), applied only to the trace-bound copy of
text before it's written to `traces/traces.jsonl` — the live API answer is
never redacted. Tolerant of typographic hyphen/dash variants (U+2010–U+2014)
that the LLM substitutes for ASCII `-` in markdown output (see §2 for the
leak this fixed).

### 1.6 Tracing & replay tooling (Week 5) — `app/services/trace_logger.py`, `scripts/`
- **`trace_logger.py`** *(new)*: writes one JSON record per request to
  `traces/traces.jsonl` — question, retrieval candidates, redacted answer,
  prompt version, model/temperature — for later sampling, audit, and replay.
- **`scripts/generate_traces.py`**: populates `traces.jsonl` with a
  deliberately varied synthetic batch (in-scope, out-of-scope, guardrail-
  triggering, ambiguous, wrong-reference, typo'd questions) through the live
  `RAGService`.
- **`scripts/seeded_sample.py`**: draws a reproducible seeded random sample
  of trace IDs for manual review.
- **`scripts/read_sample.py`**: pretty-prints a sample file for open-coding.
- **`scripts/replay_trace.py`**: replays a trace from only what's stored in
  `traces.jsonl`, writing original-vs-replay evidence to
  `analysis/replay_<trace_id>.md` (and reports where redaction makes an
  exact-value replay impossible, rather than hiding the gap).
- **`scripts/apply_task_set_d_updates.py`**: re-runs specific trace IDs
  live through the fixed retrieval/chunking pipeline in place (same
  `trace_id`), used to regenerate the 3 traces fixed in §2.

### 1.7 Multi-answer / partial-answer prompting — `app/services/llm_service.py`
`PROMPT_VERSION` bumped `v1` → `v2`. The old prompt refused to answer at all
if any part of a multi-field question was ungrounded. The new prompt (rules
2, 5, 8 in the template) requires checking each requested field
independently, answering the fields that are grounded, and explicitly
naming only the fields that aren't — instead of an all-or-nothing refusal.

### 1.8 Source deduplication — `app/services/rag_service.py`
`sources` returned to the API caller are now deduplicated by
`(page, title)` before being returned, instead of one entry per retrieved
chunk (which could repeat the same page/section multiple times).

---

## 2. Bug fixes (root-caused against real trace data, not guessed)

Both fixes were verified live (re-ran retrieval + generation with the fix
applied, on the actual failing questions), not just reviewed as code.

### 2.1 Oversized, unsplit chunks — `app/services/chunk_service.py`
`max_chunk_size` was computed in `__init__` but never enforced in
`split_large_segment`. A long run of plain-paragraph lines with no
heading/table/key-value boundary was emitted as a single unsplit chunk —
observed up to 2016 chars against a configured `chunk_size=500`. This let a
clause's subject get separated from the sentence referencing it, across a
chunk boundary. Fix: `_split_oversized_unit` splits only at sentence
boundaries, never mid-sentence.

### 2.2 Scenario-mismatched reranking — `app/services/retrieval_service.py`
A "premium paid" question was retrieving a *policy-voided-for-fraud*
forfeiture clause, ranked #1 by the cross-encoder purely on
"premium"/"claim" keyword overlap — and the LLM then asserted that clause
applied to an ordinary claim. Fix: `_apply_scenario_mismatch_penalty`
demotes scenario-gated clauses unless the question names that scenario.

### 2.3 Claim numbers leaking past redaction — `app/core/redaction.py`
The ID regex only matched an ASCII `-`. The LLM's markdown output
substitutes typographic hyphen/dash variants (U+2010–U+2014), e.g.
`CLM‑2026‑00437`, which slipped past the old pattern and leaked into
`traces/traces.jsonl` unredacted. Found via Task Set D's claim-number
deterministic check against the 30-trace baseline. Fixed by widening the
hyphen character class; the same class is reused in
`eval/deterministic_checks.py` so the check and the redaction agree on what
counts as a claim number.

**Before/after (live re-run against the 3 questions these fixes touched):**

| Question | Before | After |
|---|---|---|
| Policy type & coverage | Wrongly generalized a truncated depreciation clause to the whole vehicle | Correctly lists it as a separate, non-liability item |
| Premium vs. claim amount | Asserted "no additional amount payable" using a void-policy forfeiture clause | Honest refusal: "documents do not state the amount..." |
| Expiry vs. start date | "Start date is not included" (chunk existed, buried at fusion rank 19/20) | Correctly states both dates and that they differ |

The 3 fixed traces were regenerated in place with the **same `trace_id`**;
their original pre-fix records are archived in
`traces/pre_fix_archive_q2_q4_q7.jsonl` for evidence, and their reference
labels in `eval/labels_27.json` were deliberately left unchanged.

---

## 3. Testing / evaluation implemented

### 3.1 Retrieval quality — `eval/run_eval.py`
Ingests a chosen dataset (`ACTIVE_DATASET` in the file, one of
`acko_bike` / `shieldcare_basic` / `shieldcare_full` from
`eval/datasets.py`) and computes **hit-rate@k, recall@k, MRR** against
golden questions in `eval/questions/*.json`. Writes a report to
`eval/reports/<dataset>.md`.

### 3.2 Deterministic checks ("Task Set D") — `eval/deterministic_checks.py` *(new)*
Four objective, code-verifiable checks run on every traced answer (no LLM
involved, on purpose — grading these by LLM was found to be unreliable):
- `claim_number_format` — must match `CLM-YYYY-NNNNN`, tolerant of
  typographic hyphens.
- `date_of_loss` — must be a parseable calendar date, if stated.
- `deductible_numeric` — a stated deductible must carry a nearby numeric
  value, unless explicitly stated as absent.
- `denial_requires_exclusion` — a denial/rejection must cite the exclusion
  it falls under.
Each returns `NOT_APPLICABLE` when the field isn't mentioned at all (a
legitimate outcome, not a failure).

Unit tests: `eval/test_deterministic_checks.py` — 16 plain-assert tests
(no pytest dependency) against real strings pulled from actual traces and
source PDFs.

### 3.3 Regression harness — `eval/regression_runner.py` *(new)*
Scores all active traces in `traces/traces.jsonl` against frozen reference
labels in `eval/labels_27.json`, running the Task Set D checks on each and
overriding the reference label to `FAIL` if any deterministic check fails.
Reports pass rate overall, by mode, and by dataset, plus a per-check
PASS/FAIL/N-A breakdown, and flags any trace where a deterministic check
overrode the reference label. Writes `eval/regression_results.json`.

### 3.4 LLM-judge harness — `eval/judge_runner.py` *(new)*
For each active trace, sends `QUESTION` / `CONTEXT` (the actual post-rerank
retrieved chunks, redacted) / `ANSWER` to the configured Groq model using a
versioned judge prompt (`eval/judge_v1.txt`), parses its verdict, and
compares it against the reference label. Includes rate-limit backoff and
inter-call pacing for Groq's low tokens/minute cap.

### 3.5 Manual trace review tooling
`scripts/seeded_sample.py` + `scripts/read_sample.py` for reproducible,
seeded manual sampling and open-coding of traces (`analysis/notes.md`,
`analysis/taxonomy.md`).

### Results as of this branch

| Suite | Result |
|---|---|
| Regression — overall (`eval/regression_results.json`) | 24/27 — **88.89%** |
| Regression — mode OK | 24/24 — **100%** |
| Regression — `shieldcare_basic` / `shieldcare_full` | 9/9 — **100%** each |
| Regression — `acko_bike` | 6/9 — **66.67%** |
| Deterministic override rate | 0/27 — **0%** (no auto-check ever overrode a label) |
| Deterministic unit tests (`eval/test_deterministic_checks.py`) | 16/16 — **100%** |
| LLM judge v1 (`eval/judge_v1_results.json`) | 23/27 — **85.19%** |
| LLM judge v2 (`eval/judge_v2_results.json`, historical — prompt file no longer on disk) | 25/27 — **92.59%** |

Full breakdown and caveats: `CHANGES.md`.

---

## 4. How to run everything

### 4.1 Setup
```bash
python3 -m venv .venv
source .venv/bin/activate          # macOS/Linux
.venv\Scripts\activate             # Windows

pip install -r requirements.txt
```
Create a `.env` with at least:
```
GROQ_API_KEY=...
MODEL_NAME=...
EMBEDDING_MODEL=...
CHROMA_PATH=chroma_db
COLLECTION_NAME=...
```

### 4.2 Run the API
```bash
uvicorn app.main:app --reload
```

### 4.3 Retrieval quality eval (hit-rate@k, recall@k, MRR)
```bash
python -m eval.run_eval
```
Change `ACTIVE_DATASET` in `eval/run_eval.py` to switch between
`acko_bike`, `shieldcare_basic`, `shieldcare_full`.

### 4.4 Deterministic checks — unit tests
```bash
python -m eval.test_deterministic_checks
```

### 4.5 Regression harness (Task Set D against `traces/traces.jsonl`)
```bash
python -m eval.regression_runner
```
Writes `eval/regression_results.json`.

### 4.6 LLM-judge harness
```bash
python -m eval.judge_runner --prompt eval/judge_v1.txt --out eval/judge_v1_results.json
```

### 4.7 Trace generation, sampling, and replay
```bash
python -m scripts.generate_traces
python -m scripts.seeded_sample --seed 20260826 --n 20
python -m scripts.read_sample analysis/sample_20.json
python -m scripts.replay_trace --seed 20260824 --pick-random
python -m scripts.replay_trace --trace-id <uuid>
```

### 4.8 Re-apply the Task Set D fix set to specific traces
```bash
python -m scripts.apply_task_set_d_updates
```

---

## 5. Files worth knowing about (new on this branch)

| File | Purpose |
|---|---|
| `app/core/redaction.py` | Trace PII redaction |
| `app/services/ingestion_pipeline.py` | Shared ingest pipeline (API + eval) |
| `app/services/trace_logger.py` | Writes `traces/traces.jsonl` |
| `eval/deterministic_checks.py` | Task Set D objective checks |
| `eval/test_deterministic_checks.py` | Unit tests for the above |
| `eval/regression_runner.py` | Regression harness over labeled traces |
| `eval/judge_runner.py` | LLM-judge harness |
| `eval/datasets.py` | Registry of eval datasets/question sets |
| `scripts/generate_traces.py` | Synthetic trace population |
| `scripts/seeded_sample.py` / `read_sample.py` | Manual sampling/review |
| `scripts/replay_trace.py` | Trace replay for audit |
| `scripts/apply_task_set_d_updates.py` | Re-run fixed pipeline on specific trace IDs |
| `CHANGES.md` | Detailed changelog for the latest (Week 6) commit, with full before/after evidence |

For the deepest level of detail on the Week 6 fixes specifically (exact
root cause, verification method, full results tables), see `CHANGES.md` —
this file summarizes the whole branch (Week 4 → 6); `CHANGES.md` is
Week 6-only but goes deeper on that slice.
