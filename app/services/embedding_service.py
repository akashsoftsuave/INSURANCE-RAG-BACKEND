import math
from typing import Tuple, List, Dict, Any
from sentence_transformers import SentenceTransformer
from app.core.config import settings


class EmbeddingService:
    """
    Service responsible for generating embeddings.
    """

    def __init__(self):
        print(f"[Embedding] Loading model: {settings.EMBEDDING_MODEL}")
        self.model = SentenceTransformer(settings.EMBEDDING_MODEL)
        self.embedding_dimension = self.model.get_sentence_embedding_dimension()
        print("[Embedding] Model loaded.")

    def _validate_embedding(self, embedding: List[float], expected_dim: int) -> Tuple[bool, str]:
        """Validate an embedding vector."""
        if embedding is None:
            return False, "embedding is None"
        
        if len(embedding) != expected_dim:
            return False, f"invalid dimension: {len(embedding)} != {expected_dim}"
        
        if any(math.isnan(v) for v in embedding):
            return False, "contains NaN"
        
        if any(math.isinf(v) for v in embedding):
            return False, "contains Inf"
        
        if all(v == 0 for v in embedding):
            return False, "zero vector"
        
        norm = math.sqrt(sum(v * v for v in embedding))
        if norm < 1e-10:
            return False, "near-zero vector"
        
        return True, "valid"

    def generate_embeddings(
        self,
        chunks: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
        """
        Generate embeddings for chunks with validation.
        Chunks with invalid embeddings are dropped from the result.
        Returns (valid_chunks_with_embeddings, validation_stats)
        """
        validation_stats = {
            "missing_embeddings": 0,
            "invalid_dimensions": 0,
            "invalid_vectors": 0,
        }

        texts = [chunk["text"] for chunk in chunks]

        embeddings = self.model.encode(
            texts,
            normalize_embeddings=True,
            batch_size=32,
            show_progress_bar=False
        )

        valid_chunks = []
        for i, chunk in enumerate(chunks):
            embedding = embeddings[i].tolist()
            chunk["embedding"] = embedding

            is_valid, reason = self._validate_embedding(embedding, self.embedding_dimension)
            if not is_valid:
                if "dimension" in reason:
                    validation_stats["invalid_dimensions"] += 1
                elif "None" in reason:
                    validation_stats["missing_embeddings"] += 1
                else:
                    validation_stats["invalid_vectors"] += 1
                print(f"[Embedding] WARNING: Chunk {chunk.get('chunk_id', i)} - {reason} - skipping")
                continue

            valid_chunks.append(chunk)

        return valid_chunks, validation_stats

    def generate_query_embedding(self, text: str) -> List[float]:
        return self.model.encode(
            text,
            normalize_embeddings=True
        ).tolist()