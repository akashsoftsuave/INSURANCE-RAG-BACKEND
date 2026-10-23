# Architecture — one page

```
 FNOL text (+ claim no.)                                                       trace_id minted here (uuid4[:16])
        │                                                                              │
        ▼                                                                              │
┌───────────────────────────── python -m capstone triage  (single entry point) ───────┼───────────────────┐
│  input.sanitize  ── scrub(PII) using the claim record's own PII fields ─ [tools, $0] │                   │
│        │                                                                             ▼                   │
│        ▼                                                                                                 │
│  AGENT LOOP (hand-built, Week 7)  ── 3-4 LLM turns ── [generation, the money]  Groq gpt-oss-120b, T=0    │
│    local tools (in-process)            MCP tools (discovered via tools/list at connect, never hard-coded)│
│    ├ lookup_policy ───────────┐        ├ search_policy_wording(query, product, LOSS DATE, trace_id)        │
│    ├ check_coverage_in_force ─┤        └ get_limits(product, peril, LOSS DATE, policy_id, trace_id)        │
│    └ refer_to_adjuster ───────┘                       │  stdio JSON-RPC            ▲ trace_id echoed back  │
│                                                       ▼                            │ + server span         │
│                          ┌───────────── OUR MCP SERVER (capstone/mcp_server.py) ───┴──────────────┐       │
│                          │ date filter enforced IN CODE: effective_from <= loss <= effective_to    │       │
│                          │ Week 3-4 RetrievalService: bge-small vectors + BM25 → RRF → cross-encoder│       │
│                          │ Chroma metadata: product, clause, endorsement_id/version, effective_*    │       │
│                          │ no path to claim records · responses re-redacted · query redacted in log │       │
│                          └──────────────────────────────────────────────────────────────────────────┘       │
│        │                                                                                                 │
│        ▼                                                                                                 │
│  assemble (CODE, not the model): citations · in-force result · deductible / sub-limit / policy limit     │
│  guard: no verdict words in the model's own voice (summary AND referral reason)  ·  forced referral       │
└───────┬──────────────────────────────────────────────────────────────────────────────────────────────────┘
        ▼
  ONE request-log line (capstone/logs/requests.jsonl)  +  MCP span log (mcp_spans.jsonl)  +  referral record
  — all carry the same trace_id; spans carry hop (agent|mcp), stage, latency, tokens, cost
```

## Where the model calls happen
Only one place: the agent loop (`CapstoneAgent._llm`) and, in evals, the judge. Retrieval, reranking, the
date filter, the limits lookup, the in-force check, the guard and the PII scrub are all deterministic code. The MCP
server never calls a model.

## Where the money goes
Measured on the final run (see `reports/EVAL_REPORT.md` for the exact figures): ~100% of provider cost is the
agent's LLM turns (~9k tokens/claim before the one-turn change, fewer after). Retrieval and tools are local CPU: $0
per call, but not free in latency (the cross-encoder dominates the MCP span).

## What the model is and is not trusted with
| The model decides | Code decides |
|---|---|
| which tool to call next, and the search wording | which wording version is in force (date filter) |
| the 2-3 sentence factual summary | deductible / sub-limit / policy-limit figures (from the tables, not typed by the model) |
| | whether the policy was in force on the loss date |
| | whether the output may leave the system (guard) and that a referral always exists |
