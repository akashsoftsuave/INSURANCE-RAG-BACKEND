# Changes on `Implement/week6` vs `main`

Not named `README.md` / `Readme.md` on purpose: this repo already has a
`Readme.md`, and the two names collide on a case-insensitive filesystem
(Windows) — using either would silently overwrite the existing file.

This branch is 9 commits ahead of `main` (Week 4 → Week 6 implementation
history) plus the uncommitted work below, made on top of commit `34e8151`
("week 6 chunk chnages"). Nothing here has been committed or pushed —
`git status` on this branch will show exactly the files below.

## 1. Retrieval / chunking bug fixes

Root-caused against the actual retrieved chunks in `traces/traces.jsonl`
(not guessed), each verified by re-running retrieval + generation live
with the fix applied.

- **`app/services/chunk_service.py`** — `max_chunk_size` was computed in
  `__init__` but never enforced anywhere in `split_large_segment`, so a
  single oversized logical unit (a long run of plain-paragraph lines with
  no heading/table/key-value boundary) was emitted as one unsplit chunk —
  observed up to 2016 chars against a configured `chunk_size=500`. This
  is what let a clause's subject get separated from the sentence
  referring to it across a chunk boundary. Added
  `_split_oversized_unit`, which splits an overflowing unit **only at
  sentence boundaries** (never mid-sentence), so a clause never loses
  its own antecedent, while still respecting the configured size cap.
- **`app/services/retrieval_service.py`** — added
  `_apply_scenario_mismatch_penalty` in the reranking step: a chunk whose
  text is gated on a specific scenario (fraud / misrepresentation /
  non-disclosure / policy voided / forfeiture) is demoted unless the
  question itself names that scenario. Root cause found: a "premium
  paid" question was retrieving a *policy-voided-for-fraud* forfeiture
  clause (ranked #1 by the cross-encoder purely on "premium"/"claim"
  keyword overlap), and the LLM then asserted it applied to an ordinary
  claim — the clause never was about that.

### Verified before/after (live re-run, not just a code review)

| Question | Before | After |
|---|---|---|
| Policy type & coverage | Wrongly generalized a truncated depreciation clause to the whole vehicle | Correctly lists it as a separate, non-liability coverage item; no wrong claim |
| Premium vs. claim amount | Asserted "no additional amount payable for a claim" using a void-policy forfeiture clause | "The documents do not state the amount you would need to pay... when making a claim" (honest refusal) |
| Expiry vs. start date | "The start date is not included in the documents" (chunk existed, just buried at fusion rank 19/20) | Correctly states both expiry (10 Jun 2027) and start (11 Jun 2026) dates, and that they differ |

The three fixed traces were regenerated in place through the fixed
pipeline, **same `trace_id`**, in `traces/traces.jsonl`. Their reference
labels in `eval/labels_30.json` were intentionally left unchanged (not
updated just because the application was fixed). Their original pre-fix
trace records are archived verbatim in
`traces/pre_fix_archive_q2_q4_q7.jsonl` for evidence.

## 2. Active trace population

`traces/traces.jsonl` now holds **27** active traces.
`eval/labels_30.json` is unchanged and still has all 30 original
human-reviewed reference labels.

`eval/regression_runner.py` was updated to reflect the active
population (guard now only warns below 25, the agreed minimum) — no
other behavior changed. Verified zero verdict changes among the 27
traces relative to the prior 30-trace baseline (no regressions
introduced by the code changes above — the harness scores off the
frozen reference label, unaffected either way).

## 3. LLM-judge harness

- **`eval/judge_runner.py`** *(new)* — didn't exist before this branch.
  For each active trace, sends QUESTION/CONTEXT/ANSWER (CONTEXT =
  `retrieval.candidates[].text_redacted`, the actual post-rerank chunks
  the frozen answer was built from) to the configured Groq model, parses
  the verdict, and compares it against `eval/labels_30.json`. Includes
  rate-limit backoff and steady inter-call pacing (this project's Groq
  org has been observed capped as low as 8000 tokens/minute).
- **`eval/judge_v1_results.json`, `eval/judge_v2_results.json`** — kept
  as the last measured judge runs (population 27, agreement 85.19% →
  92.59%). **Historical, not currently reproducible as-is**: `judge_v2`
  ran against a `eval/judge_v2.txt` prompt file that no longer exists on
  disk, and `judge_v1_results.json` ran against a since-reverted version
  of `eval/judge_v1.txt` (the file on disk now differs from what
  produced that result). Re-running `eval.judge_runner` today will
  reflect the current `judge_v1.txt` only, and `--prompt eval/judge_v2.txt`
  will fail until/unless that file is recreated.

## 4. Repo hygiene (unrelated to the above, done on request)

- Untracked `chroma_db/` and `.DS_Store` from git — both are already
  listed in `.gitignore` ("regenerated from source PDFs on every
  ingest/eval run") but had been committed before that rule existed, so
  git kept tracking every local change to the vector DB binary. `git rm
  -r --cached` was used (files remain on disk, git just stops tracking
  future changes to them).
- Deleted a stray `.gitignore copy` file (an older, incomplete duplicate
  of `.gitignore`).
- Deleted scratch/verification files and throwaway Chroma collections
  used only to check the chunking/retrieval fixes before applying them.

## Results summary (all percentages)

**Regression — `eval/regression_results.json`**

| Metric | Value |
|---|---|
| Overall pass rate | 24/27 — **88.89%** |
| Pass rate — mode OK | 24/24 — **100%** |
| Pass rate — mode A | 0/1 — **0%** |
| Pass rate — mode C | 0/2 — **0%** |
| Pass rate — acko_bike | 6/9 — **66.67%** |
| Pass rate — shieldcare_basic | 9/9 — **100%** |
| Pass rate — shieldcare_full | 9/9 — **100%** |

**Deterministic checks (Task Set D, across all 27 active traces)**

| Check | Pass | Fail | Not applicable |
|---|---|---|---|
| claim_number_format | 3 | 0 | 24 |
| date_of_loss | 0 | 0 | 27 |
| deductible_numeric | 5 | 0 | 22 |
| denial_requires_exclusion | 0 | 0 | 27 |

Deterministic override rate: 0/27 — **0%** (no automated check ever
overrode a reference label).

**Deterministic unit tests — `eval/test_deterministic_checks.py`**

16/16 — **100%** pass.

**LLM judge agreement (historical, see caveat in section 3)**

| Run | Agreement |
|---|---|
| judge v1 | 23/27 — **85.19%** |
| judge v2 | 25/27 — **92.59%** |
| Improvement | **+7.4 points** |
