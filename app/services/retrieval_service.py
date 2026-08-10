from app.core.config import settings
from app.services.embedding_service import EmbeddingService
from app.services.vector_store import VectorStore


class RetrievalService:

    def __init__(self):

        self.embedding_service = EmbeddingService()
        self.vector_store = VectorStore()

    def retrieve(self, question: str):

        # Generate embedding for user question
        query_embedding = self.embedding_service.generate_query_embedding(question)

        # Search ChromaDB
        results = self.vector_store.collection.query(
            query_embeddings=[query_embedding],
            n_results=settings.TOP_K
        )

        return results