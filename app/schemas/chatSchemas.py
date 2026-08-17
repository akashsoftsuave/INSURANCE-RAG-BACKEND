from pydantic import BaseModel

class ChatRequest(BaseModel):
    question: str


class Source(BaseModel):
    page: int
    title: str | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]