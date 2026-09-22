import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.claims_agent.pricing import call_cost
from app.services.retrieval_service import RetrievalService
from app.services.llm_service import LLMService
from app.services.guardrail_service import GuardrailService
from app.services.trace_logger import TraceLogger
from app.core.redaction import redact
from app.core.config import settings


class RAGService:

    def __init__(
        self,
        trace_logger: TraceLogger | None = None,
        retriever: RetrievalService | None = None,
        llm: LLMService | None = None,
        guardrail: GuardrailService | None = None,
    ):
        # The components are injectable so the fixed and agentic flows can
        # share one RetrievalService (the cross-encoder it loads is heavy, and
        # sharing it is also what makes a fixed-vs-agentic comparison fair —
        # both read the same index through the same reranker). Every argument
        # defaults to constructing its own, so `RAGService()` is unchanged.
        self.guardrail = guardrail or GuardrailService()
        self.retriever = retriever or RetrievalService()
        self.llm = llm or LLMService()
        self.tracer = trace_logger or TraceLogger()

    def ask(self, question: str) -> Dict[str, Any]:

        started = time.monotonic()
        trace_id = TraceLogger.new_trace_id()
        guard = self.guardrail.check(question)

        if not guard["allowed"]:
            answer = "I can only answer questions related to your insurance policy documents."

            self._write_trace(
                trace_id=trace_id,
                question=question,
                guard=guard,
                retrieval_results=[],
                pre_rerank_results=[],
                generation=None,
                answer=answer,
                final_context=[],
                source_pages_used=[],
                source_pages_returned=[],
                evidence_chunks=[],
                latency_ms=int((time.monotonic() - started) * 1000),
            )

            return {
                "answer": answer,
                "sources": [],
                "trace_id": trace_id,
                "mode": "fixed",
                "rounds": 0,
                "queries": [],
                "latency_ms": int((time.monotonic() - started) * 1000),
                "tokens": {"prompt": 0, "completion": 0, "total": 0},
                "cost_usd": 0.0,
            }

        # Step 1: Retrieval
        retrieval_results, pre_rerank_results = self.retriever.retrieve(question, return_pre_rerank=True)

        # Step 2: Context Selection - currently sends all retrieved chunks (TOP_K)
        final_context_chunks = retrieval_results

        # Step 3: Build context for LLM from selected chunks
        context = "\n\n".join(r["document"] for r in final_context_chunks)

        # Step 4: Generate answer
        generation = self.llm.generate(question=question, context=context)
        answer = generation["raw_output"]

        # Step 5: Evidence-based source selection
        sources, source_pages_used, source_pages_returned, evidence_chunks = self._build_evidence_sources(
            answer, final_context_chunks, retrieval_results
        )


        # Step 7: Write trace with all observability data
        self._write_trace(
            trace_id=trace_id,
            question=question,
            guard=guard,
            retrieval_results=retrieval_results,
            pre_rerank_results=pre_rerank_results,
            generation=generation,
            answer=answer,
            final_context=final_context_chunks,
            source_pages_used=source_pages_used,
            source_pages_returned=source_pages_returned,
            evidence_chunks=evidence_chunks,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

        usage = generation.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        return {
            "answer": answer,
            "sources": sources,
            "trace_id": trace_id,
            # The fixed flow is one retrieval round on the question as written;
            # reported explicitly so a caller can compare it against agentic
            # without special-casing either shape.
            "mode": "fixed",
            "rounds": 1,
            "queries": [question],
            "latency_ms": int((time.monotonic() - started) * 1000),
            "tokens": {
                "prompt": prompt_tokens,
                "completion": completion_tokens,
                "total": prompt_tokens + completion_tokens,
            },
            "cost_usd": call_cost(settings.MODEL_NAME, prompt_tokens, completion_tokens),
        }

    def _build_evidence_sources(
        self,
        answer: str,
        final_context_chunks: List[Dict[str, Any]],
        all_retrieved_chunks: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[int], List[int], List[Dict[str, Any]]]:
        """
        Build sources based on evidence actually supporting the answer.
        Returns (sources_list, pages_used, pages_returned, evidence_chunks)
        """
        if not answer or not final_context_chunks:
            pages_used = []
            pages_returned = list(dict.fromkeys(
                r.get("metadata", {}).get("page", 1) for r in final_context_chunks
            ))
            return [], pages_used, pages_returned, []

        answer_lower = answer.lower()
        evidence_chunks = []
        pages_used = []
        seen_pages = set()

        for r in final_context_chunks:
            page = r.get("metadata", {}).get("page", 1)
            section = r.get("metadata", {}).get("section")
            doc_text = r.get("document", "").lower()
            chunk_id = r.get("id", "")

            # Check if this chunk contains evidence for the answer
            has_evidence = self._chunk_supports_answer(answer_lower, doc_text, chunk_id)

            if has_evidence:
                evidence_chunks.append({
                    "chunk_id": chunk_id,
                    "page": page,
                    "section": section if section and section != "Unknown" else None
                })
                if page not in pages_used:
                    pages_used.append(page)

        # Pages in final context (for source_pages_returned)
        pages_returned = list(dict.fromkeys(
            r.get("metadata", {}).get("page", 1) for r in final_context_chunks
        ))

        # Build source citations from evidence chunks
        sources = []
        seen = set()
        for ev in evidence_chunks:
            page = ev["page"]
            section = ev["section"]
            key = (page, section)
            if key not in seen:
                seen.add(key)
                source = {"page": page}
                if section:
                    source["title"] = section
                sources.append(source)

        # If no evidence found (shouldn't happen with proper retrieval), fall back
        if not sources and final_context_chunks:
            seen = set()
            for r in final_context_chunks:
                page = r.get("metadata", {}).get("page", 1)
                section = r.get("metadata", {}).get("section")
                title = section if section and section != "Unknown" else None
                key = (page, title)
                if key not in seen:
                    seen.add(key)
                    source = {"page": page}
                    if title:
                        source["title"] = title
                    sources.append(source)
                    if page not in pages_used:
                        pages_used.append(page)

        return sources, pages_used, pages_returned, evidence_chunks

    def _chunk_supports_answer(self, answer_lower: str, doc_text: str, chunk_id: str) -> bool:
        """
        Determine if a chunk provides evidence for the answer.
        Uses token overlap between answer and chunk as a heuristic.
        """
        if not answer_lower or not doc_text:
            return False

        # Tokenize both
        answer_tokens = set(self._tokenize(answer_lower))
        doc_tokens = set(self._tokenize(doc_text))

        if not answer_tokens:
            return False

        # Check token overlap - at least 30% of answer tokens in chunk
        overlap = len(answer_tokens & doc_tokens)
        overlap_ratio = overlap / max(len(answer_tokens), 1)

        # Also check for key phrases
        key_phrases = self._extract_key_phrases(answer_lower)
        phrase_match = any(phrase in doc_text for phrase in key_phrases)

        return overlap_ratio >= 0.3 or phrase_match

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """Simple tokenization."""
        import re
        return re.findall(r"\w+", text.lower())

    def _extract_key_phrases(self, text: str) -> List[str]:
        """Extract potential key phrases from answer."""
        phrases = []
        import re
        # Extract numbers with units
        phrases.extend(re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?\s*(?:Rs\.?|%|days?|months?|years?)", text, re.IGNORECASE))
        # Extract capitalized terms
        phrases.extend(re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", text))
        return [p for p in phrases if len(p) > 2]

    def _write_trace(
        self,
        *,
        trace_id: str,
        question: str,
        guard: Dict[str, Any],
        retrieval_results: List[Dict[str, Any]],
        pre_rerank_results: List[Dict[str, Any]],
        generation: Optional[Dict[str, Any]],
        answer: str,
        final_context: List[Dict[str, Any]],
        source_pages_used: List[int],
        source_pages_returned: List[int],
        evidence_chunks: List[Dict[str, Any]],
        latency_ms: int | None = None,
    ) -> None:

        # Build rank maps from pre_rerank_results
        retrieval_ranks = {}
        for i, r in enumerate(pre_rerank_results):
            chunk_id = r.get("id", "")
            retrieval_ranks[chunk_id] = i + 1

        # Build pre_rerank candidates with ranks
        pre_rerank_candidates = [
            {
                "chunk_id": r.get("id"),
                "page": r.get("metadata", {}).get("page"),
                "section": r.get("metadata", {}).get("section"),
                "fusion_score": r.get("score"),
                "retrieval_rank": i + 1,
                "text_redacted": redact(r.get("document", "")),
            }
            for i, r in enumerate(pre_rerank_results)
        ]

        # Build candidates with rerank info and rank changes
        candidates = [
            {
                "chunk_id": r.get("id"),
                "page": r.get("metadata", {}).get("page"),
                "section": r.get("metadata", {}).get("section"),
                "fusion_score": r.get("score"),
                "rerank_score": r.get("rerank_score"),
                "retrieval_rank": retrieval_ranks.get(r.get("id", "")),
                "rerank_rank": i + 1,
                "rank_change": (
                    retrieval_ranks.get(r.get("id", "")) - (i + 1)
                    if retrieval_ranks.get(r.get("id", "")) is not None
                    else None
                ),
                "text_redacted": redact(r.get("document", "")),
            }
            for i, r in enumerate(retrieval_results)
        ]

        # Build final_context - exact chunks sent to LLM
        final_context = [
            {
                "chunk_id": r.get("id"),
                "document": r.get("document", ""),  # Not redacted for debugging
                "page": r.get("metadata", {}).get("page"),
                "section": r.get("metadata", {}).get("section"),
                "retrieval_rank": retrieval_ranks.get(r.get("id", "")),
                "rerank_rank": i + 1,
                "rank_change": (
                    retrieval_ranks.get(r.get("id", "")) - (i + 1)
                    if retrieval_ranks.get(r.get("id", "")) is not None
                    else None
                ),
                "fusion_score": r.get("score"),
                "rerank_score": r.get("rerank_score"),
                "final_context_rank": i + 1,
                "text_redacted": redact(r.get("document", "")),
            }
            for i, r in enumerate(final_context)
        ]

        # Compute source precision using unique pages
        source_precision = 0.0
        if source_pages_returned:
            source_precision = len(set(source_pages_used) & set(source_pages_returned)) / len(source_pages_returned)

        usage = (generation or {}).get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        record = {
            "trace_id": trace_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            # Which flow produced this trace. Present on every new record so a
            # mixed trace file can be split by flow; traces written before the
            # agentic flow existed simply have no "mode" key.
            "mode": "fixed",
            "question_redacted": redact(question),
            "guardrail": guard,
            "retrieval": {
                "top_k": settings.TOP_K,
                "candidate_pool_size": len(pre_rerank_results),
                "retrieved_chunks_count": len(retrieval_results),
                "final_context_chunks_count": len(final_context),
                "pre_rerank_candidates": pre_rerank_candidates,
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
            "performance": {
                "latency_ms": latency_ms,
                "tokens_total": prompt_tokens + completion_tokens,
                "cost_usd": call_cost(settings.MODEL_NAME, prompt_tokens, completion_tokens),
                "model_calls": 1 if generation else 0,
            },
            "redaction": {
                "applied": True,
                "method": "id-regex ([A-Z]{2,10}-...-YYYY-ALPHANUM)",
                "fields": [
                    "question_redacted",
                    "retrieval.pre_rerank_candidates[].text_redacted",
                    "retrieval.candidates[].text_redacted",
                    "final_context[].text_redacted",
                    "raw_output_redacted",
                    "final_answer_redacted",
                ],
            },
        }

        self.tracer.write(record)