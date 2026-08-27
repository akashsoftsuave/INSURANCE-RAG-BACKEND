from fastapi import APIRouter

from app.schemas.chatSchemas import ChatRequest, ChatResponse
from app.services.rag_service import RAGService

router = APIRouter(
    prefix="/api/v1/chat",
    tags=["Chat"]
)

rag_service = RAGService()


@router.post("/", response_model=ChatResponse, response_model_exclude_none=True)
async def chat(request: ChatRequest):
    print(f"[Chat] POST /api/v1/chat | question={request.question!r}")
    response = rag_service.ask(request.question)

    return response