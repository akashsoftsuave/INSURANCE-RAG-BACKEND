# Notes — Eval Upgrade: 3-Document / 30-Trace Set (Insurance)

## Redaction confirmation

Claim/policy numbers are redacted **before** the trace is
written — see `RAGService._write_trace()` in `app/services/rag_service.py`,
which calls `redact()` (`app/core/redaction.py`) on every text field
(question, retrieved chunk text, model output) before `TraceLogger.write()`
is ever called. Nothing is redacted after the fact.

## Population (Requirement 2 — no sampling needed)

`traces/traces.jsonl` currently holds exactly **30 traces**, generated
against 3 source documents in `eval/data/`, 10 questions each:

| Doc | Questions | trace index |
|---|---|---|
| `acko_bike.pdf` | 1–10 | Q1–Q10 |
| `shieldcare_basic.pdf` | 11–20 | Q11–Q20 |
| `shieldcare_full.pdf` | 21–30 | Q21–Q30 |

Population size (30) is small enough, and already partitioned by design
(10/doc), that a random sub-sample would throw away signal rather than add
rigor — so this round open-codes the **full population**, not a drawn
sample. `scripts/seeded_sample.py` remains the right tool once real
production traffic accumulates in `traces/traces.jsonl` and the population
grows past what's practical to read end-to-end.

## Replay evidence (Requirement 1)

Trace replayed: `57e00158-da17-4636-ac8a-1ef363c9b445` (Q7, ACKO —
"When exactly does my policy expire, and is the expiry date the same as
the policy start date?"), chosen because it's one of the confirmed
failures and the most useful thing to prove is that the failure is
**deterministic**, not an LLM flake.

Full evidence: `analysis/replay_57e00158-da17-4636-ac8a-1ef363c9b445.md`,
generated with:
`python -m scripts.replay_trace --trace-id 57e00158-da17-4636-ac8a-1ef363c9b445`

Result: the replay reproduces the original answer almost verbatim —
"Your policy expires on 10 June 2027 ... the documents you provided do
not include the policy's start date." Cross-checking the retrieved
chunk IDs recorded in the trace (`25, 4, 18, 8, 17`) against the corpus
confirms the chunk that actually contains the start date
("Policy Starts 2026 11 June", chunk `1`) is **never in the final top-5**
for this question — it's in the pre-rerank candidate pool but doesn't
survive reranking. So this isn't a generation hallucination or
nondeterminism; it's a reproducible retrieval gap, and re-running it
fixes nothing on its own.

Nothing had to be reconstructed beyond what `replay_trace.py` already
flags (redacted IDs can't be reproduced verbatim; Groq temperature=0 is
not bit-for-bit guaranteed) — neither affected this trace.

## Open-coding — all 30 traces (Requirement 3)

One honest sentence per trace, what was SEEN, not a category or a fix.
Read alongside the raw Q/A pairs with:
`python -m scripts.read_sample analysis/sample_30.json` (or read
`traces/traces.jsonl` directly — every trace is in-scope this round).

### ACKO Bike (Q1–Q10)

| # | trace_id | Observation |
|---|---|---|
| 1 | `b4549815` | Correctly returns the policy number and bike registration number, both verbatim in the top-retrieved chunk. |
| 2 | `199fd82b` | Correctly identifies the policy as Third-Party-only, then adds an unprompted "50% depreciation deduction" line generalized from a chunk fragment whose subject ("Tyres and tubes...") was cut off by the previous chunk boundary — the retrieved fragment reads as if the deduction applies to "the vehicle" broadly. |
| 3 | `b2ef8fe2` | Correctly returns nominee name and relationship. |
| 4 | `b88b0456` | Correctly states the ₹843 premium, then concludes "no additional amount payable for a claim" — a conclusion drawn from a chunk about premium forfeiture on a **void** policy, a different scenario than an ordinary claim, which the retrieved context does not actually address. |
| 5 | `9d971abf` | Correctly names the previous insurer and correctly declines to speculate on its effect on the current policy — no retrieved chunk addresses that. |
| 6 | `265971ca` | Correctly returns the ₹1,00,000 third-party property damage limit. |
| 7 | `57e00158` | Correctly states the expiry date (present in context) but says the start date "is not included in the documents" — the start-date chunk exists in the corpus but isn't in this query's top-5 (confirmed by replay above). |
| 8 | `cade1f7a` | Correctly returns the IRDAI registration number. |
| 9 | `215e9ac9` | Says the requested claim-documentation checklist "couldn't be found" — accurate; no chunk in the ACKO corpus lists what to submit with a claim. |
| 10 | `4aa90d04` | Says the entire summary (bike, policy type, premium, nominee, liability limit, dates) "couldn't be found," even though this query's own retrieved context includes chunks naming the bike, policy number, premium clause, and expiry date. |

### ShieldCare Basic (Q11–Q20)

| # | trace_id | Observation |
|---|---|---|
| 11 | `81bd1491` | Correctly returns the beneficiary name; policy number is redacted rather than answered, as expected. |
| 12 | `e61a25bc` | Correctly returns the 100% beneficiary percentage. |
| 13 | `a4d8dcdb` | Correctly returns the deductible and correctly distinguishes it from co-pay — but the raw retrieved chunk itself already renders the rupee sign as a bare "I" (e.g. "I10,000") rather than ₹, so the model is repeating a source-data defect, not introducing one. |
| 14 | `32c41bcd` | Correctly returns the hospitalization claim amount and coverage limit, again passing through the source chunk's "I" in place of ₹. |
| 15 | `c62c0880` | Correctly returns the diagnostic coverage limit and claim amount. |
| 16 | `6e6434b6` | Correctly reports the diagnostic claim as "Under Review," not approved, and cites the claims table as evidence. |
| 17 | `a0be337e` | Correctly states cosmetic procedures are excluded absent an endorsement. |
| 18 | `69db754e` | Correctly explains the diagnostic co-pay and correctly distinguishes it from a deductible. |
| 19 | `70297e14` | Correctly returns the ambulance coverage limit, again showing the source "I10,000" instead of ₹10,000. |
| 20 | `c2572dc5` | Answers 5 of 6 requested summary sections correctly and precisely, but says expiry "couldn't be found" even though this document has an explicit expiry date — the expiry chunk simply isn't among the 5 retrieved for this query. |

### ShieldCare Full (Q21–Q30)

| # | trace_id | Observation |
|---|---|---|
| 21 | `a518dd80` | Correctly returns all three beneficiaries with percentages and relationships. |
| 22 | `a9ef481c` | Correctly repeats the three beneficiary percentages. |
| 23 | `983ef060` | Correctly returns the deductible and correctly explains what it means for a claim. |
| 24 | `b5d7be71` | Correctly returns the hospitalization claim amount and coverage limit — this document's currency renders as "Rs." cleanly, unlike shieldcare_basic's "I". |
| 25 | `b95829b9` | Correctly returns the diagnostic limit and lists both diagnostic claims with dates, amounts, and status in a table. |
| 26 | `bc3d9e98` | Correctly identifies which of the two diagnostic claims is approved vs. under review. |
| 27 | `8ca605c2` | Correctly states cosmetic procedures are excluded, quoting the exclusion wording and a real rejected-claim example. |
| 28 | `266c8000` | Correctly states the diagnostic co-pay and correctly notes no separate deductible is listed for diagnostics. |
| 29 | `a70efd3e` | Correctly returns the ambulance coverage limit and "no deductible" detail. |
| 30 | `26ee3af3` | Answers 5 of 6 sections in a well-organized fact/not-established split, but again labels expiry as "not mentioned," even though this document does contain an expiry date — same missed-chunk pattern as Q20. |

**24/30 pass, 6/30 fail (80%)** — see `analysis/taxonomy.md` for how the
6 failures cluster into modes.

## Dated prediction (Requirement 5)

- Date: 2026-09-01
- Mode targeted: Mode A — expiry/date chunk missing from top-k for
  multi-field or "summarize everything" queries (see taxonomy.md)
- Specific change: raise `TOP_K` in `eval/run_eval.py` / the live
  retrieval path from 5 to 8 for the final (post-rerank) context window,
  so a low-ranked-but-relevant date chunk has more room to survive.
- Predicted delta: Mode A instances drop from 3/30 (Q7, Q20, Q30) to at
  most 1/30 — widening the window doesn't fix chunk-boundary truncation
  (Mode C), so Q2/Q4 are expected to be unaffected by this change.
- Git commit hash (committed before the fix): `cf77483b37e4014ca89d8006d254c8303a681c00`

## Why a public benchmark would have missed this (Requirement 6)

A public RAG benchmark ships its own documents, its own chunking, and
generic questions — it would never encounter `shieldcare_basic.pdf`'s
specific PDF-extraction defect (₹ decoding as "I"), never reproduce this
app's exact `chunk_size=500/overlap=100` boundary that severs the
antecedent of "they" in the ACKO depreciation clause, and would have no
reason to phrase multi-field "summarize everything" questions the way a
real policyholder does, which is precisely the query shape that starves
the date chunk out of the top-k here. All three failure modes are
artifacts of this app's specific corpus and chunking, not of RAG or this
model in general, so a generic benchmark score would have reported this
system as healthy while it was quietly wrong on 1 in 5 questions.
