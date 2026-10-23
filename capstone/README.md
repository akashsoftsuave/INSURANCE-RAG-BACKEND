# Claims Triage & Coverage Assistant — Week 12 capstone (Variant D)

Triages a motor/household FNOL for a human adjuster: finds the policy, checks it was in force **on the loss date**,
retrieves the policy wording **in force on that date** (not the best-ranking wording), cites the clause **and the
endorsement version**, reports **deductible, sub-limit and policy limit as three separate numbers**, and **refers to an
adjuster**. It never states a coverage decision.

## Run it (from a clean clone)

```bash
python -m venv venv && venv\Scripts\activate        # or source venv/bin/activate
pip install -r requirements.txt
# .env needs GROQ_API_KEY, MODEL_NAME, EMBEDDING_MODEL (see repo root; same file the earlier weeks use)

python -m capstone setup                              # builds the synthetic corpus + ingests the wording (once, ~1 min)
python -m capstone triage "Claim CLM-2024-4471A. My car was damaged in the flood on 2024-08-11. It was parked outside when the water rose." --full
python -m capstone eval                               # the whole eval suite on the integrated system (slow: see below)
python -m capstone eval --stage score                 # re-score stored runs without any new agent calls
python -m capstone replay <trace_id>                  # prove the evidence behind a logged answer is reproducible
python -m capstone serve-mcp                          # our MCP server on stdio, for an external client
python capstone/examples/external_client.py --repo <this repo's absolute path>
```

Single entry point: `python -m capstone <command>` (`capstone/__main__.py`). The HTTP route
(`POST /api/v1/capstone/triage`, `GET /api/v1/capstone/clause/...`) is mounted in `app/main.py` and calls the same agent.

**Timing warning:** the model tier is capped at 8,000 tokens/minute and one claim costs thousands of tokens, so
`eval` spends most of its time waiting on the rate limiter (36 cases ≈ 30–60 min). Runs are stored in `capstone/runs/`
and resumable; scoring is instant.

## What is where

| Path | What |
|---|---|
| `capstone/agent.py` | the hand-built agent loop (W7) + MCP client (W9) + tracing (W11) |
| `capstone/mcp_server.py` | **our** MCP server: date-scoped wording search + limits table |
| `capstone/retrieval.py` | date-aware retrieval over the W3–4 `RetrievalService` (`mode=in_force` vs `naive`) |
| `capstone/tools.py` | local tools: policy lookup, coverage-in-force, refer-to-adjuster |
| `capstone/guard.py`, `pii.py` | no-verdict guard, PII scrub |
| `capstone/requestlog.py`, `replay.py` | one log line per claim; reproduce the evidence from a trace id |
| `capstone/corpus.py` | the synthetic corpus builder (see limits below) |
| `capstone/evals/` | cases, assertions, judge, trajectory eval, retrieval-level metric |
| `capstone/docs/` | `SEAMS.md`, `ARCHITECTURE.md`, `WEAKNESSES.md`, `TAXONOMY.md`, `FAILURE_LOOP.md`, `TEN_X_PLAN.md`, `DEMO_SCRIPT.md`, `FRESH_EYES_NOTE.md` |
| `capstone/reports/` | `EVAL_REPORT.md`, `suite_results.json`, `version_metric.json`, `judge_agreement.md` |
| `capstone/evidence/` | the raw traces the write-ups cite |

## Read this before trusting any number

The brief's corpus (60-page wordings, real 2023/2025 endorsement PDFs, 500 real anonymised claim files) is **not in
this repo**. `capstone/corpus.py` generates a synthetic stand-in with the same structure and the same trap (the flood
and theft clauses are rewritten with effect from 2025-03-01 and the newer text ranks better). Every figure in the
reports is therefore a statement about *this system on this synthetic corpus*, not about a real insurer's book.
`EVAL_REPORT.md` lists exactly what the suite does not cover.
