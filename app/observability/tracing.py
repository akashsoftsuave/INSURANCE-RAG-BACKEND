"""Per-span request tracing.

Every request is one RequestTrace made of Spans. A span is one stage of the
pipeline and carries its own latency, tokens and cost, tagged with the stage
it belongs to (retrieval / generation / tools). Cost-by-stage is therefore a
sum over spans, not a number derived after the fact — which is what lets a
cost change be attributed to the stage that caused it.

Billing note: retrieval (bge-small embeddings, BM25, ms-marco cross-encoder)
and the "tools" stage (guardrail, evidence/source builder) run on local CPU
inside this process. They have no per-call provider charge, so their
cost_usd is 0.0 and their `billing` attribute says why, instead of the
number silently being zero.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

STAGES = ("retrieval", "generation", "tools")


@dataclass
class Span:
    name: str
    stage: str
    start_offset_ms: float
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "stage": self.stage,
            "start_offset_ms": round(self.start_offset_ms, 1),
            "latency_ms": round(self.latency_ms, 1),
            "tokens": {
                "prompt": self.prompt_tokens,
                "completion": self.completion_tokens,
                "total": self.prompt_tokens + self.completion_tokens,
            },
            "cost_usd": round(self.cost_usd, 8),
            "attrs": self.attrs,
        }


class RequestTrace:
    def __init__(self, trace_id: str):
        self.trace_id = trace_id
        self._t0 = time.monotonic()
        self.spans: list[Span] = []

    @contextmanager
    def span(self, name: str, stage: str, **attrs):
        if stage not in STAGES:
            raise ValueError(f"stage must be one of {STAGES}, got {stage!r}")
        started = time.monotonic()
        s = Span(name=name, stage=stage,
                 start_offset_ms=(started - self._t0) * 1000, attrs=dict(attrs))
        try:
            yield s
        finally:
            s.latency_ms = (time.monotonic() - started) * 1000
            self.spans.append(s)

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self._t0) * 1000

    def totals(self) -> dict:
        by_stage = {st: {"latency_ms": 0.0, "tokens": 0, "cost_usd": 0.0} for st in STAGES}
        for s in self.spans:
            b = by_stage[s.stage]
            b["latency_ms"] += s.latency_ms
            b["tokens"] += s.prompt_tokens + s.completion_tokens
            b["cost_usd"] += s.cost_usd
        for b in by_stage.values():
            b["latency_ms"] = round(b["latency_ms"], 1)
            b["cost_usd"] = round(b["cost_usd"], 8)
        return {
            "latency_ms": round(self.elapsed_ms(), 1),
            "tokens": sum(b["tokens"] for b in by_stage.values()),
            "cost_usd": round(sum(b["cost_usd"] for b in by_stage.values()), 8),
            "by_stage": by_stage,
        }

    def to_list(self) -> list[dict]:
        return [s.to_dict() for s in self.spans]
