from datetime import datetime, timezone

from app.services.retrieval_service import RetrievalService
from app.services.llm_service import LLMService
from app.services.guardrail_service import GuardrailService
from app.services.trace_logger import TraceLogger
from app.core.redaction import redact
from app.core.config import settings


class RAGService:

    def __init__(self, trace_logger: TraceLogger | None = None):

        self.guardrail = GuardrailService()
        self.retriever = RetrievalService()
        self.llm = LLMService()
        self.tracer = trace_logger or TraceLogger()

    def ask(self, question: str):

        trace_id = TraceLogger.new_trace_id()
        guard = self.guardrail.check(question)

        if not guard["allowed"]:
            answer = "I can only answer questions related to your insurance policy documents."

            # Redaction happens here, on the copy that goes to disk, before
            # the write call below — never as a cleanup pass afterwards.
            self._write_trace(
                trace_id=trace_id,
                question=question,
                guard=guard,
                results=[],
                pre_rerank_results=[],
                generation=None,
                answer=answer,
            )

            return {
                "answer": answer,
                "sources": [],
                "trace_id": trace_id,
            }

        results, pre_rerank_results = self.retriever.retrieve(question, return_pre_rerank=True)

        # New retrieval format: list of {id, document, metadata, score}
        documents = [r["document"] for r in results]
        metadatas = [r["metadata"] for r in results]

        context = "\n\n".join(documents)

        generation = self.llm.generate(question=question, context=context)
        answer = generation["raw_output"]

        sources = []
        seen = set()
        for meta in metadatas:
            page = meta.get("page", 1)
            section = meta.get("section")
            title = section if section and section != "Unknown" else None
            key = (page, title)
            if key not in seen:
                seen.add(key)
                source = {"page": page}
                if title:
                    source["title"] = title
                sources.append(source)

        self._write_trace(
            trace_id=trace_id,
            question=question,
            guard=guard,
            results=results,
            pre_rerank_results=pre_rerank_results,
            generation=generation,
            answer=answer,
        )

        return {
            "answer": answer,
            "sources": sources,
            "trace_id": trace_id,
        }

    def _write_trace(self, *, trace_id, question, guard, results, pre_rerank_results, generation, answer):

        pre_rerank_candidates = [
            {
                "chunk_id": r.get("id"),
                "page": r.get("metadata", {}).get("page"),
                "section": r.get("metadata", {}).get("section"),
                "fusion_score": r.get("score"),
                "text_redacted": redact(r.get("document", "")),
            }
            for r in pre_rerank_results
        ]

        candidates = [
            {
                "chunk_id": r.get("id"),
                "page": r.get("metadata", {}).get("page"),
                "section": r.get("metadata", {}).get("section"),
                "fusion_score": r.get("score"),
                "rerank_score": r.get("rerank_score"),
                "text_redacted": redact(r.get("document", "")),
            }
            for r in results
        ]

        record = {
            "trace_id": trace_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "question_redacted": redact(question),
            "guardrail": guard,
            "retrieval": {
                "top_k": settings.TOP_K,
                "candidate_pool_size": len(pre_rerank_results),
                "pre_rerank_candidates": pre_rerank_candidates,
                "candidates": candidates,
            },
            "prompt_version": generation["prompt_version"] if generation else None,
            "model": {
                "name": generation["model"],
                "temperature": generation["temperature"],
                "provider": "groq",
            } if generation else None,
            "raw_output_redacted": redact(answer),
            "final_answer_redacted": redact(answer),
            "redaction": {
                "applied": True,
                "method": "id-regex ([A-Z]{2,10}-...-YYYY-ALPHANUM)",
                "fields": [
                    "question_redacted",
                    "retrieval.pre_rerank_candidates[].text_redacted",
                    "retrieval.candidates[].text_redacted",
                    "raw_output_redacted",
                    "final_answer_redacted",
                ],
            },
        }

        self.tracer.write(record)