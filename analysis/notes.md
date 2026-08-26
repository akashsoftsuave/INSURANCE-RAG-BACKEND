# Notes — Week 5 Task Set D (Insurance)

## Redaction confirmation

Claim/policy numbers are redacted **before** the trace is
written — see `RAGService._write_trace()` in `app/services/rag_service.py`,
which calls `redact()` (`app/core/redaction.py`) on every text field
(question, retrieved chunk text, model output) before `TraceLogger.write()`
is ever called. Nothing is redacted after the fact.

## Seeded random sample

- Seed: `TODO — paste the --seed value you passed to scripts/seeded_sample.py`
- Population size at draw time: `TODO`
- 20 sampled trace_ids:
  1. TODO
  2. ...
  20. ...

(Generated with `python -m scripts.seeded_sample --seed <SEED> --n 20`,
which writes the same list to `analysis/sample_20.json`.)

## Replay evidence (Requirement 1)

Trace replayed: `TODO trace_id`
Full evidence: see `analysis/replay_<trace_id>.md`, generated with
`python -m scripts.replay_trace --trace-id <id>` (or `--pick-random --seed <n>`).

Summary of what had to be added / could not be reconstructed:
- TODO (fill in from the "Fields that could not be reconstructed" section
  of the generated replay report — e.g. redacted context means exact
  policy/claim numbers in the original answer can't be reproduced verbatim).

## Open-coding — 20 sentences (Requirement 3)

One honest sentence per trace describing what was SEEN, not a category or a
fix. Generate the reading view with:
`python -m scripts.read_sample analysis/sample_20.json`

| # | trace_id | Observation |
|---|---|---|
| 1 | TODO | TODO |
| 2 | TODO | TODO |
| 3 | TODO | TODO |
| 4 | TODO | TODO |
| 5 | TODO | TODO |
| 6 | TODO | TODO |
| 7 | TODO | TODO |
| 8 | TODO | TODO |
| 9 | TODO | TODO |
| 10 | TODO | TODO |
| 11 | TODO | TODO |
| 12 | TODO | TODO |
| 13 | TODO | TODO |
| 14 | TODO | TODO |
| 15 | TODO | TODO |
| 16 | TODO | TODO |
| 17 | TODO | TODO |
| 18 | TODO | TODO |
| 19 | TODO | TODO |
| 20 | TODO | TODO |

## Dated prediction (Requirement 5)

- Date: TODO
- Mode targeted: TODO
- Specific change: TODO
- Predicted delta: TODO (e.g. "mode X drops from N% to under M%")
- Git commit hash (committed before the fix): TODO

## Why a public benchmark would have missed this (Requirement 6)

TODO — 3 sentences.
