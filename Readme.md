# Insurance RAG — backend

## Commands

```bash
python -m eval.run_eval                              # eval run

python3 -m venv .venv
source .venv/bin/activate                            # macOS / Linux
.venv\Scripts\activate                               # windows

python -m scripts.seeded_sample --seed 20260826 --n 20
python -m scripts.read_sample analysis/sample_20.json

python run_rag_modes.py "what is the excess and what is the sum insured?"
python run_rag_modes.py --mode agentic "list all exclusions"
python run_rag_modes.py --mode auto --collection eval_acko_bike "who is the nominee?"
```

---

# Local (uncommitted) changes — Week 7: agentic RAG alongside the fixed pipeline

On branch `week-7`, on top of commit `6199363`. Nothing below is committed.

The change adds a **second retrieval flow** (multi-round, model-planned) next to
the existing one-shot pipeline, plus the routing, per-answer metrics and tracing
needed to compare the two. Everything is additive: `RAGService()` with no
arguments, and a `POST /api/v1/chat` body with no `mode`, behave exactly as
before.

## New files

### `app/services/agentic_rag_service.py` (new, 565 lines)

The agentic loop. Where the fixed flow is `retrieve once -> answer`, this one is:

1. **plan** — a model call decomposes the question into a minimum set of focused
   search queries (one query per requested fact; insurer vocabulary rather than
   conversational phrasing).
2. **retrieve** — every pending query goes through the **same**
   `RetrievalService` (hybrid BM25 + vector, RRF fusion, cross-encoder rerank,
   scenario-mismatch penalty). None of the retrieval stack is re-implemented.
3. **sufficiency** — a model call reads the accumulated evidence pool and says
   whether it answers every part of the question; if not, it proposes follow-up
   queries worded *differently* from the ones already tried.
4. **repeat** — until sufficient, out of new queries, or out of budget.
5. **answer** — one generation over the pooled evidence.

Design points worth knowing:

- `AgenticRAGService` **subclasses** `RAGService` only to inherit
  `_build_evidence_sources`, so the citation/evidence logic is identical in both
  flows — which is what makes them comparable. `ask()` is fully overridden; the
  fixed pipeline is never executed from here.
- **Bounded from the start.** `AgenticConfig` defaults: `max_rounds=3`,
  `max_queries_per_round=3`, `max_context_chunks=12`, plus a `BudgetConfig`
  (`max_iters=4`, `max_tokens=20_000`, `max_cost=0.02`,
  `max_wall_clock_ms=20_000`) using the same `BudgetTracker` as the Week-7 claims
  agent. An unbounded retrieval loop is the main failure mode of agentic RAG.
- **Every failure degrades toward the fixed flow.** An unparseable planner
  response falls back to searching the raw question; an unparseable sufficiency
  response is treated as "sufficient" and stops the loop; a failed control call
  returns `None` instead of raising. A malformed control response can never take
  the loop down.
- **The evidence pool dedupes by chunk id** across rounds. A chunk found by
  several queries keeps its *best* rerank score (each score is relative to its own
  query) and records every query that found it in `found_by`.
- Exact re-runs of an already-tried query are skipped (`duplicate_query`) instead
  of burning a round.
- `stop_reason` is always recorded: `sufficient`, `max_rounds`, `no_new_queries`,
  `budget:<limit>`, `sufficiency_unparsed`, `sufficiency_disabled`,
  `guardrail_blocked`.
- Planner/sufficiency prompts are versioned by `AGENTIC_POLICY_VERSION`
  (`"agentic-loop-v1"`), so a trace can be attributed to a loop policy. The answer
  prompt is unchanged and still versioned by `LLMService.PROMPT_VERSION`.
- The agentic trace record is **key-for-key compatible** with the fixed one:
  `retrieval.candidates` is the union pool across all rounds, and `final_context`
  is what actually went to the model. That is what lets `eval/judge_runner.py` and
  `eval/regression_runner.py` read an agentic trace unchanged. It then adds an
  `agentic` block (policy version, total rounds, per-round queries and new-chunk
  counts, sufficiency verdicts, stop reason, config).

### `app/services/rag_router.py` (new, 152 lines)

Mode routing, with three modes:

| mode | behavior |
|---|---|
| `fixed` | always the one-shot pipeline (`RAGService`) |
| `agentic` | always the multi-round loop (`AgenticRAGService`) |
| `auto` | the **backend** decides, per question |

- The `auto` decision lives here and nowhere else. The frontend may *request* a
  mode, but never sees the heuristic — only which flow ran (`mode` on the
  response, `routing` in the trace).
- `classify()` is a **deterministic keyword/shape heuristic, not an LLM call**, on
  purpose: spending a model call to decide whether to spend model calls adds
  latency to every question, including the majority that one retrieval round
  already answers. Signals: `multi_part`, `comparison`, `enumeration`,
  `multiple_questions` (more than one `?`), `long_question` (>18 tokens). Any
  signal firing routes to agentic; none firing routes to fixed. All signals plus
  an `explanation` string are returned and recorded, so a wrong route can be
  diagnosed after the fact.
- `RAGRouter` builds **one** `RetrievalService` / `LLMService` /
  `GuardrailService` / `TraceLogger` and hands the same instances to both flows.
  Two retrievers would load the heavy cross-encoder twice, and sharing one
  guarantees both flows search the same index through the same reranker — which
  is what makes the comparison meaningful.

### `run_rag_modes.py` (new, 65 lines)

CLI to ask one question through either or both flows. `--mode both` (the default)
runs fixed then agentic back to back and prints a comparison line (latency ×,
tokens ×, rounds). `--collection` points at another Chroma collection.
`--trace-file` defaults to `traces/manual_runs.jsonl`, deliberately **not**
`traces/traces.jsonl`, which holds the frozen 27-trace evaluation baseline.

## Modified files

### `app/api/chat.py`

- Routes through `RAGRouter` instead of a bare `RAGService`.
- `ChatRequest.mode` is logged and passed through.
- Catches Groq `RateLimitError` and returns **429** with an explanatory message
  instead of an unhandled 500. The provider's per-minute token cap is low enough
  to hit during normal UI use, and agentic mode spends several calls per question.
- New `GET /api/v1/chat/modes` returns the valid modes, the default, and a
  one-line description of each — so the frontend's mode selector is driven by the
  backend rather than a hard-coded list on the client.

### `app/schemas/chatSchemas.py`

- `ChatRequest.mode: Literal["fixed", "agentic", "auto"] = "fixed"` — defaults to
  `fixed`, so existing clients that send no mode keep the previous behavior.
- `ChatResponse` gains optional observability fields: `mode`, `requested_mode`,
  `rounds`, `queries`, `latency_ms`, `tokens` (new `TokenUsage` model), `cost_usd`
  and `routing`. All optional, and the route uses `response_model_exclude_none`,
  so the response shape stays backward compatible.

### `app/services/rag_service.py`

- **Dependency injection**: `retriever`, `llm` and `guardrail` are now constructor
  arguments, each defaulting to constructing its own. `RAGService()` is unchanged;
  the router uses this to share components with the agentic flow.
- `ask()` now also returns `mode: "fixed"`, `rounds: 1`, `queries: [question]`,
  `latency_ms`, `tokens` and `cost_usd` — reported explicitly so a caller can
  compare fixed against agentic without special-casing either shape. The
  guardrail-blocked early return reports the same keys with zeros.
- Traces gain `"mode": "fixed"` (so a mixed trace file can be split by flow;
  traces written before the agentic flow existed simply have no `mode` key) and a
  `performance` block (`latency_ms`, `tokens_total`, `cost_usd`, `model_calls`).

### `app/services/llm_service.py`

Returns `usage: {prompt_tokens, completion_tokens}` from the Groq response, read
defensively (`getattr(..., 0) or 0`). Purely additive — nothing about the
generation itself changes.

### `app/services/retrieval_service.py`

`RetrievalService(collection_name=None)` passes the collection through to
`VectorStore`, so an eval harness can point the same retriever at a per-dataset
collection. `RetrievalService()` behaves exactly as before.

## Data files touched

- `traces/traces.jsonl` — **5 records appended** from manual API testing
  (3 `mode: "fixed"`, 2 `mode: "agentic"`). The 31 pre-existing records are
  untouched and carry no `mode` key. These 5 are test runs, not part of the frozen
  27-trace evaluation baseline.
- `chroma_db/` — local vector-store binaries, regenerated on ingest; not a source
  change.
