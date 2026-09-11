"""Agentic RAG: a multi-round retrieval loop that sits alongside the fixed
one-shot pipeline in app/services/rag_service.py.

Where the fixed flow is a straight line (retrieve once -> answer), this
flow lets the model decide:

  1. plan        -- decompose the question into one or more focused queries
  2. retrieve    -- run every pending query through the SAME RetrievalService
  3. sufficiency -- read the accumulated evidence and say whether it answers
                    the question; if not, propose follow-up queries
  4. repeat      -- until sufficient, out of new queries, or out of budget
  5. answer      -- one generation over the accumulated evidence pool

Everything retrieval-side is reused verbatim (hybrid BM25 + vector, RRF
fusion, cross-encoder rerank, scenario-mismatch penalty). The only thing
this module adds is the control loop around it.

Budgets are the same BudgetTracker the Week-7 claims agent uses
(app/claims_agent/budgets.py) -- an unbounded retrieval loop is the main
failure mode of agentic RAG, so it is bounded from the start.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.claims_agent.budgets import BudgetConfig, BudgetTracker
from app.claims_agent.pricing import call_cost
from app.core.config import settings
from app.core.redaction import redact
from app.services.guardrail_service import GuardrailService
from app.services.llm_service import LLMService
from app.services.rag_service import RAGService
from app.services.retrieval_service import RetrievalService
from app.services.trace_logger import TraceLogger

# Bump when PLANNER_PROMPT or SUFFICIENCY_PROMPT below changes, so a trace
# can be told apart from one produced by an earlier loop policy. The answer
# prompt itself is unchanged and still versioned by LLMService.PROMPT_VERSION.
AGENTIC_POLICY_VERSION = "agentic-loop-v1"

PLANNER_PROMPT = """You plan document retrieval for an insurance question-answering system.

Break the user's question into the minimum set of focused search queries needed
to answer it completely. Rules:

- A question asking for ONE fact needs exactly ONE query.
- A question asking for SEVERAL distinct facts (for example "what is X and what
  is Y", a comparison, or a multi-part question) needs ONE query PER fact.
- Write each query as the terms that would appear in the policy document, not as
  a conversational sentence. Prefer the insurer's vocabulary: "excess" or
  "deductible" for out-of-pocket amounts, "exclusions" for what is not covered,
  "sum insured" for coverage limits, "period of insurance" for dates.
- Never write more than {max_queries} queries.
- Do not answer the question. Only plan the searches.

Output strictly this JSON and nothing else:
{{"queries": ["...", "..."]}}"""

SUFFICIENCY_PROMPT = """You check whether retrieved insurance-document excerpts are
sufficient to fully answer a question. You do NOT answer the question.

Given a QUESTION and the EVIDENCE retrieved so far, decide:

- sufficient = true  if EVIDENCE contains enough to answer every part of the
  question, INCLUDING the case where the evidence shows the document genuinely
  does not cover a requested fact (an honest "not stated" is a complete answer,
  so do not keep searching for something the document plainly does not contain).
- sufficient = false if some part of the question is still unaddressed AND a
  different search phrasing could plausibly find it.

When sufficient = false, list the specific missing facts, and write follow-up
queries that use DIFFERENT wording from the queries already tried -- repeating a
failed query wastes a round. Never write more than {max_queries} follow-ups.

Output strictly this JSON and nothing else:
{{"sufficient": true/false, "missing": ["..."], "followup_queries": ["..."]}}"""


@dataclass
class AgenticConfig:
    """Loop shape and budget. Defaults are deliberately tight: the point of
    the loop is a second look when the first one missed, not open-ended
    search."""

    max_rounds: int = 3
    max_queries_per_round: int = 3
    max_context_chunks: int = 12
    enable_planner: bool = True
    enable_sufficiency: bool = True
    # Budget for the loop's own model calls (planner + sufficiency) plus the
    # final generation. Looser than the claims agent's default because the
    # answer generation alone carries a large context.
    budget: BudgetConfig = field(
        default_factory=lambda: BudgetConfig(
            max_iters=4,
            max_tokens=20_000,
            max_cost=0.02,
            max_wall_clock_ms=20_000,
        )
    )


def _parse_json_object(raw: str) -> dict | None:
    """The loop's control calls ask for bare JSON, but models still wrap it in
    prose or fences often enough that a tolerant parse is worth it. A None
    return means the caller falls back to a deterministic default -- a
    malformed control response must never take the loop down."""
    if not raw:
        return None
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _normalize_query(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "").strip().lower())


class AgenticRAGService(RAGService):
    """Multi-round retrieval over the same components as RAGService.

    Subclasses RAGService purely to inherit `_build_evidence_sources` -- the
    evidence/citation logic is identical in both flows and must stay that way
    for the two to be comparable. `ask()` is fully overridden; the fixed
    pipeline is never executed from here.
    """

    def __init__(
        self,
        trace_logger: TraceLogger | None = None,
        retriever: RetrievalService | None = None,
        llm: LLMService | None = None,
        guardrail: GuardrailService | None = None,
        config: AgenticConfig | None = None,
    ):
        super().__init__(
            trace_logger=trace_logger,
            retriever=retriever,
            llm=llm,
            guardrail=guardrail,
        )
        self.config = config or AgenticConfig()

    # ------------------------------------------------------------------
    # Control-plane model calls (planner / sufficiency)
    # ------------------------------------------------------------------

    def _control_call(self, tracker: BudgetTracker, system_prompt: str, user_message: str) -> dict | None:
        """One planner/sufficiency call. Reuses the LLMService Groq client so
        the whole app holds a single client. Token usage is charged to the loop
        budget as a side cost -- these calls support the loop but are not
        themselves retrieval rounds."""
        try:
            response = self.llm.client.chat.completions.create(
                model=settings.MODEL_NAME,
                temperature=0,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
        except Exception as e:
            print(f"[AgenticRAG] control call failed ({type(e).__name__}: {e}) - falling back")
            return None

        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        tracker.add_side_cost(
            prompt_tokens + completion_tokens,
            call_cost(settings.MODEL_NAME, prompt_tokens, completion_tokens),
        )
        return _parse_json_object(response.choices[0].message.content)

    def _plan_queries(self, tracker: BudgetTracker, question: str) -> tuple[List[str], bool]:
        """Returns (queries, planner_used). Falls back to the raw question --
        which is exactly what the fixed flow searches -- whenever planning is
        disabled or the planner response is unusable."""
        if not self.config.enable_planner:
            return [question], False

        obj = self._control_call(
            tracker,
            PLANNER_PROMPT.format(max_queries=self.config.max_queries_per_round),
            f"QUESTION:\n{question}",
        )
        if not obj:
            return [question], False

        queries = [q for q in obj.get("queries", []) if isinstance(q, str) and q.strip()]
        if not queries:
            return [question], False
        return queries[: self.config.max_queries_per_round], True

    def _check_sufficiency(
        self, tracker: BudgetTracker, question: str, pool: List[Dict[str, Any]], tried: List[str]
    ) -> dict:
        evidence = "\n\n".join(
            f"[chunk {i + 1}] {c['document']}"
            for i, c in enumerate(pool[: self.config.max_context_chunks])
        )
        tried_block = "\n".join(f"- {q}" for q in tried)
        obj = self._control_call(
            tracker,
            SUFFICIENCY_PROMPT.format(max_queries=self.config.max_queries_per_round),
            f"QUESTION:\n{question}\n\nQUERIES ALREADY TRIED:\n{tried_block}\n\nEVIDENCE:\n{evidence}",
        )
        if not obj:
            # An unparseable sufficiency response must not trigger another
            # round: stopping here degrades to the fixed flow's behavior, which
            # is the safe direction to fail in.
            return {"sufficient": True, "missing": [], "followup_queries": [], "parsed": False}

        return {
            "sufficient": bool(obj.get("sufficient", True)),
            "missing": [m for m in obj.get("missing", []) if isinstance(m, str)],
            "followup_queries": [
                q for q in obj.get("followup_queries", []) if isinstance(q, str) and q.strip()
            ],
            "parsed": True,
        }

    # ------------------------------------------------------------------
    # Evidence pool
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_into_pool(pool: List[Dict[str, Any]], results: List[Dict[str, Any]], query: str) -> int:
        """Merge one query's results into the running pool, deduping by chunk
        id. A chunk found by several queries keeps its BEST rerank score (each
        score is relative to its own query, so the max is the strongest single
        piece of evidence that this chunk is relevant). Returns the number of
        chunks that were not already in the pool."""
        by_id = {c["id"]: c for c in pool}
        new_count = 0
        for r in results:
            chunk_id = r.get("id")
            if chunk_id is None:
                continue
            existing = by_id.get(chunk_id)
            if existing is None:
                entry = dict(r)
                entry["found_by"] = [query]
                pool.append(entry)
                by_id[chunk_id] = entry
                new_count += 1
            else:
                if query not in existing["found_by"]:
                    existing["found_by"].append(query)
                if (r.get("rerank_score") or 0) > (existing.get("rerank_score") or 0):
                    existing["rerank_score"] = r.get("rerank_score")
                    existing["score"] = r.get("score")
        return new_count

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def ask(self, question: str) -> Dict[str, Any]:
        started = time.monotonic()
        trace_id = TraceLogger.new_trace_id()
        tracker = BudgetTracker(config=self.config.budget)

        guard = self.guardrail.check(question)
        if not guard["allowed"]:
            answer = "I can only answer questions related to your insurance policy documents."
            latency_ms = int((time.monotonic() - started) * 1000)
            self._write_agentic_trace(
                trace_id=trace_id,
                question=question,
                guard=guard,
                pool=[],
                rounds=[],
                generation=None,
                answer=answer,
                sources_bundle=([], [], [], []),
                stop_reason="guardrail_blocked",
                planner_used=False,
                tracker=tracker,
                latency_ms=latency_ms,
            )
            return {
                "answer": answer,
                "sources": [],
                "trace_id": trace_id,
                "mode": "agentic",
                "rounds": 0,
                "queries": [],
                "latency_ms": latency_ms,
                "tokens": {"prompt": 0, "completion": 0, "total": 0},
                "cost_usd": 0.0,
            }

        pool: List[Dict[str, Any]] = []
        rounds: List[Dict[str, Any]] = []
        tried: List[str] = []
        tried_norm: set[str] = set()

        pending, planner_used = self._plan_queries(tracker, question)
        stop_reason = "max_rounds"

        for round_index in range(1, self.config.max_rounds + 1):
            budget_hit = tracker.check()
            if budget_hit:
                stop_reason = f"budget:{budget_hit}"
                break

            round_queries: List[Dict[str, Any]] = []
            round_new_chunks = 0

            for query in pending[: self.config.max_queries_per_round]:
                norm = _normalize_query(query)
                if norm in tried_norm:
                    # The loop already searched this exact phrasing; re-running
                    # it would return the same chunks and burn a round.
                    round_queries.append({"query": query, "skipped": "duplicate_query"})
                    continue
                tried_norm.add(norm)
                tried.append(query)

                results, pre_rerank = self.retriever.retrieve(query, return_pre_rerank=True)
                added = self._merge_into_pool(pool, results, query)
                round_new_chunks += added
                round_queries.append({
                    "query": query,
                    "retrieved": len(results),
                    "new_chunks": added,
                    "candidate_pool_size": len(pre_rerank),
                    "chunk_ids": [r.get("id") for r in results],
                })

            pool.sort(key=lambda c: c.get("rerank_score") or 0, reverse=True)

            round_record = {
                "round": round_index,
                "queries": round_queries,
                "new_chunks": round_new_chunks,
                "pool_size": len(pool),
            }

            if round_index >= self.config.max_rounds:
                rounds.append(round_record)
                stop_reason = "max_rounds"
                break

            if not self.config.enable_sufficiency:
                rounds.append(round_record)
                stop_reason = "sufficiency_disabled"
                break

            verdict = self._check_sufficiency(tracker, question, pool, tried)
            round_record["sufficiency"] = verdict
            rounds.append(round_record)

            if verdict["sufficient"]:
                stop_reason = "sufficient" if verdict["parsed"] else "sufficiency_unparsed"
                break

            followups = [q for q in verdict["followup_queries"] if _normalize_query(q) not in tried_norm]
            if not followups:
                # The checker wants more but has nothing new to search for.
                # Another round would repeat itself -- stop and answer.
                stop_reason = "no_new_queries"
                break
            pending = followups

        context_chunks = pool[: self.config.max_context_chunks]
        context = "\n\n".join(c["document"] for c in context_chunks)

        generation = self.llm.generate(question=question, context=context)
        answer = generation["raw_output"]

        usage = generation.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        tracker.record_iteration(
            prompt_tokens,
            completion_tokens,
            call_cost(settings.MODEL_NAME, prompt_tokens, completion_tokens),
        )

        # Same evidence/citation logic as the fixed flow (inherited, not
        # reimplemented) so sources are comparable between the two.
        sources_bundle = self._build_evidence_sources(answer, context_chunks, pool)
        sources = sources_bundle[0]
        latency_ms = int((time.monotonic() - started) * 1000)

        self._write_agentic_trace(
            trace_id=trace_id,
            question=question,
            guard=guard,
            pool=pool,
            rounds=rounds,
            generation=generation,
            answer=answer,
            sources_bundle=sources_bundle,
            stop_reason=stop_reason,
            planner_used=planner_used,
            tracker=tracker,
            latency_ms=latency_ms,
            context_chunks=context_chunks,
        )

        snap = tracker.snapshot()
        return {
            "answer": answer,
            "sources": sources,
            "trace_id": trace_id,
            "mode": "agentic",
            "rounds": len(rounds),
            "queries": tried,
            "latency_ms": latency_ms,
            "tokens": {
                "prompt": prompt_tokens,
                "completion": completion_tokens,
                "total": snap["tokens_total"],
            },
            "cost_usd": snap["cost_total"],
        }

    # ------------------------------------------------------------------
    # Tracing
    # ------------------------------------------------------------------

    def _write_agentic_trace(
        self,
        *,
        trace_id: str,
        question: str,
        guard: Dict[str, Any],
        pool: List[Dict[str, Any]],
        rounds: List[Dict[str, Any]],
        generation: Dict[str, Any] | None,
        answer: str,
        sources_bundle: tuple,
        stop_reason: str,
        planner_used: bool,
        tracker: BudgetTracker,
        latency_ms: int,
        context_chunks: List[Dict[str, Any]] | None = None,
    ) -> None:
        """Writes a record that is key-for-key compatible with the fixed flow's
        trace, then adds the agentic block.

        `retrieval.candidates` is the union pool across all rounds and
        `final_context` is what was actually sent to the model -- the same
        contract the fixed flow has, which is what lets eval/judge_runner.py and
        eval/regression_runner.py read an agentic trace unchanged.
        """
        _, source_pages_used, source_pages_returned, evidence_chunks = sources_bundle
        context_chunks = context_chunks if context_chunks is not None else []

        candidates = [
            {
                "chunk_id": c.get("id"),
                "page": c.get("metadata", {}).get("page"),
                "section": c.get("metadata", {}).get("section"),
                "fusion_score": c.get("score"),
                "rerank_score": c.get("rerank_score"),
                "found_by": c.get("found_by", []),
                "pool_rank": i + 1,
                "text_redacted": redact(c.get("document", "")),
            }
            for i, c in enumerate(pool)
        ]

        final_context = [
            {
                "chunk_id": c.get("id"),
                "document": c.get("document", ""),
                "page": c.get("metadata", {}).get("page"),
                "section": c.get("metadata", {}).get("section"),
                "fusion_score": c.get("score"),
                "rerank_score": c.get("rerank_score"),
                "found_by": c.get("found_by", []),
                "final_context_rank": i + 1,
                "text_redacted": redact(c.get("document", "")),
            }
            for i, c in enumerate(context_chunks)
        ]

        source_precision = 0.0
        if source_pages_returned:
            source_precision = len(set(source_pages_used) & set(source_pages_returned)) / len(source_pages_returned)

        snap = tracker.snapshot()
        all_queries = [q["query"] for r in rounds for q in r["queries"] if "skipped" not in q]

        record = {
            "trace_id": trace_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "agentic",
            "question_redacted": redact(question),
            "guardrail": guard,
            "retrieval": {
                "top_k": settings.TOP_K,
                "candidate_pool_size": len(pool),
                "retrieved_chunks_count": len(pool),
                "final_context_chunks_count": len(final_context),
                # No separate pre-rerank stage exists at the loop level: each
                # round's rerank happens inside RetrievalService. Kept as an
                # empty list so readers of the fixed schema don't KeyError.
                "pre_rerank_candidates": [],
                "candidates": candidates,
            },
            "final_context": final_context,
            "evidence_chunks": evidence_chunks,
            "source_pages_used": source_pages_used,
            "source_pages_returned": source_pages_returned,
            "source_precision": source_precision,
            "prompt_version": generation["prompt_version"] if generation else None,
            "model": {
                "name": generation["model"],
                "temperature": generation["temperature"],
                "provider": "groq",
            } if generation else None,
            "raw_output_redacted": redact(answer),
            "final_answer_redacted": redact(answer),
            "agentic": {
                "policy_version": AGENTIC_POLICY_VERSION,
                "planner_used": planner_used,
                "total_rounds": len(rounds),
                "total_queries": len(all_queries),
                "queries": all_queries,
                "stop_reason": stop_reason,
                "rounds": rounds,
                "config": {
                    "max_rounds": self.config.max_rounds,
                    "max_queries_per_round": self.config.max_queries_per_round,
                    "max_context_chunks": self.config.max_context_chunks,
                    "enable_planner": self.config.enable_planner,
                    "enable_sufficiency": self.config.enable_sufficiency,
                },
            },
            "performance": {
                "latency_ms": latency_ms,
                "tokens_total": snap["tokens_total"],
                "cost_usd": snap["cost_total"],
                "model_calls": snap["iterations"],
            },
            "redaction": {
                "applied": True,
                "method": "id-regex ([A-Z]{2,10}-...-YYYY-ALPHANUM)",
                "fields": [
                    "question_redacted",
                    "retrieval.candidates[].text_redacted",
                    "final_context[].text_redacted",
                    "raw_output_redacted",
                    "final_answer_redacted",
                ],
            },
        }

        self.tracer.write(record)
