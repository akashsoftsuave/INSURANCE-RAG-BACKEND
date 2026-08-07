from fastapi import APIRouter

from app.schemas.chatSchemas import ChatRequest, ChatResponse
from app.services.rag_service import RAGService

router = APIRouter(
    prefix="/api/v1/chat",
    tags=["Chat"]
)

rag_service = RAGService()


@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest):
    return rag_service.ask(request.question)