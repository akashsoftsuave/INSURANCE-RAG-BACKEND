# Cost per query, by stage

Request: `26b1fd68-3b77-4220-b363-4a6fdbd4e8b1` · 2026-10-08T17:19:11.292+00:00 · user `ADJ-0106` · prompt `insurance-qa-v2` · model `openai/gpt-oss-120b`
Pricing: $0.15/1M input, $0.6/1M output (app/claims_agent/pricing.py).

## This request (the drill's found trace)

| Stage | Spans | Latency (ms) | Tokens (prompt / completion) | Cost (USD) | Share of cost |
|---|---|---|---|---|---|
| retrieval | retrieval.hybrid_rerank | 324.2 | 0 / 0 | $0.00000000 | 0% |
| generation | generation.llm | 1775.9 | 850 / 338 | $0.00033030 | 100% |
| tools | guardrail.check, tools.evidence_sources | 0.6 | 0 / 0 | $0.00000000 | 0% |
| **total** | | **2100.8** | **1188** | **$0.00033030** | 100% |

Generation arithmetic: 850 × $0.15/1M + 338 × $0.6/1M = $0.00033030.

Retrieval (bge-small embed + Chroma vector + BM25 + ms-marco cross-encoder rerank) and tools (guardrail, evidence/source builder) run on local CPU in-process: **$0 provider cost per call**, which is why they are 0 here and not missing. Their cost is latency, shown above; retrieval sub-stages for this request: embed_ms=27.6, vector_ms=3.0, bm25_ms=3.3, fuse_ms=0.1, rerank_ms=290.3.

## All logged requests in the window (29 requests)

| Stage | Mean cost/query | Mean latency (ms) | p95 latency (ms) |
|---|---|---|---|
| retrieval | $0.00000000 | 297.9 | 369.7 |
| generation | $0.00023515 | 1409.7 | 3721.1 |
| tools | $0.00000000 | 0.6 | 1.3 |
| **total** | **$0.00023515** | 1708.3 | 4080.6 |
