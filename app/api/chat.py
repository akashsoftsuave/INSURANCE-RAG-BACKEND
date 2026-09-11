from fastapi import APIRouter, HTTPException
from groq import RateLimitError

from app.schemas.chatSchemas import ChatRequest, ChatResponse
from app.services.rag_router import VALID_MODES, RAGRouter

router = APIRouter(
    prefix="/api/v1/chat",
    tags=["Chat"]
)

# One router holds both flows over a single shared RetrievalService/LLMService.
rag_router = RAGRouter()


@router.post("/", response_model=ChatResponse, response_model_exclude_none=True)
async def chat(request: ChatRequest):
    print(f"[Chat] POST /api/v1/chat | mode={request.mode} | question={request.question!r}")

    try:
        response = rag_router.ask(request.question, mode=request.mode)
    except RateLimitError as e:
        # The provider's per-minute token cap is low enough that a UI can hit
        # it during normal use, and agentic mode spends several calls per
        # question. Surface it as a 429 the client can explain, instead of an
        # unhandled 500.
        raise HTTPException(
            status_code=429,
            detail="The model provider's rate limit was reached. Wait a few "
                   "seconds and try again — agentic mode uses several model "
                   "calls per question, so it hits this sooner than fixed mode.",
        ) from e

    return response


@router.get("/modes")
async def modes():
    """The modes a client may request. Exposed so the frontend's mode selector
    is driven by the backend rather than a hard-coded list on the client."""
    return {
        "modes": list(VALID_MODES),
        "default": "fixed",
        "descriptions": {
            "fixed": "One retrieval round on the question as written, then answer.",
            "agentic": "Plan queries, retrieve over multiple rounds, check evidence sufficiency, then answer.",
            "auto": "The backend classifies the question and picks fixed or agentic.",
        },
    }
