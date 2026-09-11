"""Mode routing between the fixed and agentic RAG flows.

Three modes:

  fixed   -- always the one-shot pipeline (RAGService)
  agentic -- always the multi-round loop (AgenticRAGService)
  auto    -- the backend decides, per question

The frontend may *request* a mode, but the decision for `auto` is made
here and nowhere else: the client never sees the heuristic, it only sees
which flow ran (`mode` on the response, and `routing` in the trace).

`auto` is a deterministic keyword/shape heuristic rather than an LLM call
on purpose. Spending a model call to decide whether to spend model calls
costs latency on every single question, including the majority that one
retrieval round already answers. The signals below are cheap, inspectable,
and recorded in the trace so a wrong route can be diagnosed after the fact.
"""

from __future__ import annotations

import re
from typing import Any, Dict

from app.services.agentic_rag_service import AgenticConfig, AgenticRAGService
from app.services.guardrail_service import GuardrailService
from app.services.llm_service import LLMService
from app.services.rag_service import RAGService
from app.services.retrieval_service import RetrievalService
from app.services.trace_logger import TraceLogger

FIXED = "fixed"
AGENTIC = "agentic"
AUTO = "auto"
VALID_MODES = (FIXED, AGENTIC, AUTO)

# A question with several distinct requested facts needs one search per fact;
# a single retrieval round has to serve them all from one ranked list, which is
# where the fixed flow loses a buried fact (the Week-6 "expiry vs start date"
# failure was exactly this shape).
_MULTI_PART = re.compile(
    r"\band\b|\balso\b|\bas well as\b|\bboth\b|\beach\b|\ball of\b|"
    r"\bplus\b|;|,\s*and\b",
    re.IGNORECASE,
)
_COMPARISON = re.compile(
    r"\bvs\.?\b|\bversus\b|\bcompare[ds]?\b|\bdifference between\b|"
    r"\bdiffer\b|\bhigher than\b|\blower than\b|\bmore than\b|\brather than\b",
    re.IGNORECASE,
)
_ENUMERATION = re.compile(
    r"\blist\b|\bwhat are\b|\bwhich ones\b|\ball the\b|\bevery\b|\btotal\b|"
    r"\bbreak ?down\b|\bsummar(?:y|ize|ise)\b",
    re.IGNORECASE,
)
_LONG_QUESTION_TOKENS = 18


def classify(question: str) -> Dict[str, Any]:
    """Decide which flow a question should take under `auto`.

    Returns the decision plus the signals behind it, so the trace records
    *why* a question was routed the way it was.
    """
    text = question or ""
    question_marks = text.count("?")
    token_count = len(re.findall(r"\w+", text))

    signals = {
        "multi_part": bool(_MULTI_PART.search(text)),
        "comparison": bool(_COMPARISON.search(text)),
        "enumeration": bool(_ENUMERATION.search(text)),
        "multiple_questions": question_marks > 1,
        "long_question": token_count > _LONG_QUESTION_TOKENS,
        "token_count": token_count,
    }

    fired = [k for k, v in signals.items() if k != "token_count" and v]
    mode = AGENTIC if fired else FIXED

    return {
        "mode": mode,
        "signals": signals,
        "fired": fired,
        "explanation": (
            f"routed to {mode}: " + (", ".join(fired) if fired else "no multi-fact signals")
        ),
    }


class RAGRouter:
    """Holds one instance of each flow over ONE shared set of components.

    Building two RetrievalService instances would load the cross-encoder
    twice; sharing it also guarantees both flows search the same index with
    the same reranker, which is what makes the comparison meaningful.
    """

    def __init__(
        self,
        trace_logger: TraceLogger | None = None,
        collection_name: str | None = None,
        agentic_config: AgenticConfig | None = None,
    ):
        retriever = RetrievalService(collection_name=collection_name)
        llm = LLMService()
        guardrail = GuardrailService()
        tracer = trace_logger or TraceLogger()

        self.fixed = RAGService(
            trace_logger=tracer, retriever=retriever, llm=llm, guardrail=guardrail
        )
        self.agentic = AgenticRAGService(
            trace_logger=tracer,
            retriever=retriever,
            llm=llm,
            guardrail=guardrail,
            config=agentic_config,
        )

    def ask(self, question: str, mode: str = FIXED) -> Dict[str, Any]:
        requested = (mode or FIXED).lower()
        if requested not in VALID_MODES:
            raise ValueError(f"unknown mode {mode!r}; expected one of {VALID_MODES}")

        if requested == AUTO:
            decision = classify(question)
            resolved = decision["mode"]
            routing = {
                "requested_mode": AUTO,
                "resolved_mode": resolved,
                "routed_by": "auto_classifier",
                **decision,
            }
        else:
            resolved = requested
            routing = {
                "requested_mode": requested,
                "resolved_mode": resolved,
                "routed_by": "explicit",
                "signals": {},
                "fired": [],
                "explanation": f"caller explicitly requested {requested}",
            }

        print(f"[Router] mode={requested} -> {resolved} | {routing['explanation']}")

        service = self.agentic if resolved == AGENTIC else self.fixed
        result = service.ask(question)
        result["requested_mode"] = requested
        result["routing"] = routing
        return result
