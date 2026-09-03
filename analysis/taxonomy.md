# Taxonomy — Insurance Claims Assistant (Eval Upgrade, 3-Document Set)

Population: all 30 traces in `traces/traces.jsonl` (10 per document —
`acko_bike.pdf`, `shieldcare_basic.pdf`, `shieldcare_full.pdf`), open-coded
in full in `notes.md`. 24/30 passed; the 6 failures cluster into 3 modes.
A 4th, non-failing data-quality defect is tracked separately below since
the current eval harness can't see it.

| Mode | Count | % of 30 | Severity | Example trace_id |
|---|---|---|---|---|
| A — date/expiry chunk absent from top-k on multi-field queries | 3 | 10% | wrongly denies known info — erodes trust, no financial harm | `57e00158` (Q7) |
| B — blanket refusal despite partial supporting context | 1 | 3% | high — total information loss on an otherwise-answerable question | `4aa90d04` (Q10) |
| C — chunk topically adjacent but doesn't answer the question; model overreaches anyway | 2 | 7% | highest — produces a confident, wrong claim-scope statement | `199fd82b` (Q2), `b88b0456` (Q4) |

## How these clusters were formed

Clustering came from the 30 open-coding sentences in `notes.md`, grouped
after the fact by mechanism, not decided in advance:

- **Mode A** (Q7, Q20, Q30): in every case the answer is internally
  consistent and states real facts it *does* have, but explicitly denies
  one date field that exists in the source document. Confirmed by
  inspecting the recorded `retrieval.candidates` chunk IDs against the
  corpus — the date-bearing chunk is in the pre-rerank pool but never in
  the final top-5 — and confirmed reproducible via
  `scripts/replay_trace.py` on Q7 (see notes.md). This is the only mode
  that repeats identically across two different source documents
  (shieldcare_basic and shieldcare_full both drop the expiry chunk on the
  same "summarize everything" question shape), which is what makes it a
  systematic retrieval-ranking issue rather than a one-off.

- **Mode B** (Q10): the one case where the model refuses the *entire*
  answer rather than answering the parts it has. This is the sharpest
  contrast in the whole set — Q20 and Q30 ask the same kind of
  multi-field "only what's explicitly stated" question and correctly
  return 5 of 6 sections while flagging just the missing one, but Q10
  collapses to "I couldn't find this information" even though its own
  retrieved context contains the bike name, policy number, premium
  clause, and expiry date. Whatever makes the "explicit-only" instruction
  degrade into all-or-nothing behavior did not fire on Q20/Q30 — worth
  isolating what's different about Q10's context (denser field list, or
  the missing nominee chunk specifically) before proposing a fix.

- **Mode C** (Q2, Q4): the retrieved chunk is genuinely in context and
  genuinely about a related topic, but doesn't answer the question asked
  — the model treats it as if it does anyway. For Q2, the chunk itself
  is truncated mid-sentence ("...accident along with the vehicle damage,
  they will be covered with a 50% depreciation cut") because the
  `chunk_size=500/overlap=100` boundary cut off the sentence's subject in
  the previous chunk, so the model had no way to know "they" meant
  tyres/tubes specifically, not the vehicle generally. For Q4, the
  retrieved forfeiture-on-void clause is about a different scenario than
  the question asked, and the model used it anyway. This is the
  costliest mode for a claims assistant, since the output reads as
  confident and specific rather than uncertain.

## Data-quality defect tracked outside the pass/fail count

`shieldcare_basic.pdf` extracts its ₹ symbol as a bare "I" in every
currency figure (chunks 3, 5, and others — see Q13/14/19/20 in
notes.md), while `shieldcare_full.pdf` uses ASCII "Rs." natively and is
unaffected. This never fails the automated eval because
`expected_answer_contains` checks never test the currency symbol, so it's
currently invisible to `eval/run_eval.py` — but every answer sourced from
that document shows the policyholder a broken amount (e.g. "I10,000"
instead of "₹10,000"). Worth a `PDFLoader` fix and a follow-up ingestion
re-run rather than a prompt change, since the defect is in the extracted
text itself, not in generation.
