from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str
    # The frontend picks a mode for testing; the routing decision for "auto"
    # is made in app/services/rag_router.py, never on the client. Defaults to
    # "fixed" so existing clients that send no mode keep the previous behavior.
    mode: Literal["fixed", "agentic", "auto"] = "fixed"


class Source(BaseModel):
    page: int
    title: str | None = None


class TokenUsage(BaseModel):
    prompt: int = 0
    completion: int = 0
    total: int = 0


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
    trace_id: str | None = None

    # Observability for the mode comparison. All optional so the response
    # shape stays backward compatible (the route uses
    # response_model_exclude_none, so unset fields are simply absent).
    mode: str | None = None
    requested_mode: str | None = None
    rounds: int | None = None
    queries: list[str] | None = None
    latency_ms: int | None = None
    tokens: TokenUsage | None = None
    cost_usd: float | None = None
    routing: dict[str, Any] | None = Field(
        default=None,
        description="Why this question was routed to the flow that ran it.",
    )
