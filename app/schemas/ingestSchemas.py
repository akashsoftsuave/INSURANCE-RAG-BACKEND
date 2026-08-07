from pydantic import BaseModel


class IngestResponse(BaseModel):
    message: str
    filename: str
    total_pages: int
    total_chunks: int
    embedding_dimension: int