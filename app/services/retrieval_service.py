import re
from collections import defaultdict
from rank_bm25 import BM25Okapi

from app.core.config import settings
from app.services.embedding_service import EmbeddingService
from app.services.vector_store import VectorStore


class RetrievalService:

    SCENARIO_CONDITION_MARKERS = re.compile(
        r"void\s+ab\s+initio|void\s+from\s+inception|misrepresentation|\bfraud\b|"
        r"non[-\s]?disclosure|(?:stand\s+)?fully\s+forfeited|"
        r"no\s+obligation\s+whatsoever\s+to\s+settle",
        re.IGNORECASE,
    )
    QUERY_CONDITION_TERMS = re.compile(
        r"\bfraud\b|misrepresent|non[-\s]?disclosure|\bvoid\b|forfeit|"
        r"false\s+information|incorrect\s+information|\blied\b|lying|"
        r"not\s+disclos",
        re.IGNORECASE,
    )
    SCENARIO_MISMATCH_PENALTY = 10.0

    def __init__(self, collection_name: str | None = None):

        # collection_name lets an eval harness point the same retriever at a
        # per-dataset collection. None keeps the configured default, so
        # `RetrievalService()` behaves exactly as before.
        self.embedding_service = EmbeddingService()
        self.vector_store = VectorStore(collection_name=collection_name)

        self.cross_encoder = None
        try:
            from sentence_transformers import CrossEncoder
            print("[Retrieval] Loading cross-encoder: cross-encoder/ms-marco-MiniLM-L-6-v2")
            self.cross_encoder = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
            print("[Retrieval] Cross-encoder loaded.")
        except Exception as e:
            print(f"[Retrieval] Cross-encoder unavailable, will fall back to keyword proxy: {e}")

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Tokenize text into lowercase alphanumeric tokens."""
        return re.findall(r"\w+", text.lower())

    def _bm25_search(self, question: str, k: int = 10):
        all_data = self.vector_store.collection.get()
        documents = all_data.get("documents", [])
        ids = all_data.get("ids", [])
        metadatas = all_data.get("metadatas", [])

        if not documents:
            return []

        tokenized_corpus = [self._tokenize(doc) for doc in documents]
        bm25 = BM25Okapi(tokenized_corpus)
        query_tokens = self._tokenize(question)

        if not query_tokens:
            return []

        scores = bm25.get_scores(query_tokens)

        ranked_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        results = []
        for idx in ranked_indices:
            results.append({
                "id": ids[idx],
                "document": documents[idx],
                "metadata": metadatas[idx] if idx < len(metadatas) else {},
                "score": float(scores[idx])
            })
        return results

    def _rrf_fusion(self, vector_results: list, bm25_results: list, k: int = 60):
        """Reciprocal Rank Fusion to combine vector and BM25 results."""
        merged_scores = defaultdict(float)
        candidate_k = settings.TOP_K * 4

        # Build in-memory lookup map to avoid multiple ChromaDB queries
        doc_lookup = {}
        for r in vector_results + bm25_results:
            doc_id = r.get("id")
            if doc_id and doc_id not in doc_lookup:
                doc_lookup[doc_id] = {
                    "document": r.get("document", ""),
                    "metadata": r.get("metadata", {})
                }

        for rank, result in enumerate(vector_results, 1):
            doc_id = result.get("id")
            if doc_id:
                merged_scores[doc_id] += 1.0 / (k + rank)

        for rank, result in enumerate(bm25_results, 1):
            doc_id = result.get("id")
            if doc_id:
                merged_scores[doc_id] += 1.0 / (k + rank)

        sorted_ids = sorted(merged_scores.items(), key=lambda x: x[1], reverse=True)[:candidate_k]

        fused_results = []
        for doc_id, _ in sorted_ids:
            if doc_id in doc_lookup:
                fused_results.append({
                    "id": doc_id,
                    "document": doc_lookup[doc_id]["document"],
                    "metadata": doc_lookup[doc_id]["metadata"],
                    "score": merged_scores[doc_id]
                })

        return fused_results

    def retrieve(self, question: str, return_pre_rerank: bool = False):
        total_docs = self.vector_store.collection.count()
        if total_docs == 0:
            return ([], []) if return_pre_rerank else []

        candidate_k = min(settings.TOP_K * 4, total_docs)

        query_embedding = self.embedding_service.generate_query_embedding(question)

        vector_results = self.vector_store.collection.query(
            query_embeddings=[query_embedding],
            n_results=candidate_k
        )

        all_docs = vector_results.get("documents", [[]])[0] if vector_results.get("documents") else []
        all_metas = vector_results.get("metadatas", [[]])[0] if vector_results.get("metadatas") else []
        all_ids = vector_results.get("ids", [[]])[0] if vector_results.get("ids") else []
        all_distances = vector_results.get("distances", [[]])[0] if vector_results.get("distances") else []

        vector_formatted = []
        for i in range(len(all_ids)):
            vector_formatted.append({
                "id": all_ids[i],
                "document": all_docs[i] if i < len(all_docs) else "",
                "metadata": all_metas[i] if i < len(all_metas) else {},
                "score": float(all_distances[i]) if i < len(all_distances) else 0.0
            })

        bm25_results = self._bm25_search(question, k=candidate_k)

        fused_results = self._rrf_fusion(vector_formatted, bm25_results, k=60)

        # Snapshot pre-rerank state before `_rerank` mutates scores and reorders in place.
        pre_rerank_results = [dict(r) for r in fused_results]

        if fused_results:
            fused_results = self._rerank(fused_results, question, use_cross_encoder=True)

        final_results = fused_results[:settings.TOP_K]

        if return_pre_rerank:
            return final_results, pre_rerank_results
        return final_results

    def _apply_scenario_mismatch_penalty(self, results: list, question: str):
        if self.QUERY_CONDITION_TERMS.search(question or ""):
            return
        for r in results:
            if self.SCENARIO_CONDITION_MARKERS.search(r.get("document", "")):
                r["rerank_score"] -= self.SCENARIO_MISMATCH_PENALTY

    def _rerank(self, results: list, question: str, use_cross_encoder: bool = False):
        """Reranking using cross-encoder if available, otherwise keyword-based proxy."""
        if use_cross_encoder and self.cross_encoder is not None:
            pairs = [(question, r["document"]) for r in results]
            scores = self.cross_encoder.predict(pairs)
            for i, r in enumerate(results):
                r["rerank_score"] = float(scores[i])
            self._apply_scenario_mismatch_penalty(results, question)
            results.sort(key=lambda x: x["rerank_score"], reverse=True)
            return results

        q_tokens = set(self._tokenize(question))
        for r in results:
            doc_tokens = set(self._tokenize(r["document"]))
            r["rerank_score"] = len(q_tokens & doc_tokens) / max(len(q_tokens), 1)
        self._apply_scenario_mismatch_penalty(results, question)
        results.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
        return results