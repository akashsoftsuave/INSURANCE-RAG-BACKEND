from sentence_transformers import SentenceTransformer
from app.core.config import settings

class EmbeddingService:
    """
    Service responsible for generating embeddings.
    """

    def __init__(self):

        self.model = SentenceTransformer(
            settings.EMBEDDING_MODEL
        )

    def generate_embeddings(
        self,
        chunks: list[dict]
    ) -> list[dict]:

        for chunk in chunks:

            embedding = self.model.encode(
                chunk["text"],
                normalize_embeddings=True
            )

            chunk["embedding"] = embedding.tolist()

        return chunks

    def generate_query_embedding(self, text: str) -> list[float]:

        return self.model.encode(
            text,
            normalize_embeddings=True
        ).tolist()