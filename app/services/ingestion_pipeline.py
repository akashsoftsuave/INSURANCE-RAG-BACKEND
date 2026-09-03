from pathlib import Path
from typing import Any, Dict, Optional

from app.services.pdf_loader import PDFLoader
from app.services.chunk_service import ChunkService
from app.services.embedding_service import EmbeddingService
from app.services.vector_store import VectorStore


def run_ingestion_pipeline(pdf_path: Path, collection_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Run the PDF -> chunks -> embeddings -> vector store pipeline.
    Shared by the /ingest API route and the offline eval script so the
    two don't drift out of sync with each other.
    """
    pdf_loader = PDFLoader()
    pages = pdf_loader.extract_text(str(pdf_path))

    chunk_service = ChunkService(chunk_size=500, chunk_overlap=100)
    chunks, chunk_stats = chunk_service.create_chunks(pages, document=pdf_path.name)

    embedding_service = EmbeddingService()
    chunks, embed_stats = embedding_service.generate_embeddings(chunks)

    vector_store = VectorStore(collection_name=collection_name)
    vector_store.clear_collection()
    vector_store.add_documents(chunks)

    return {
        "pages": pages,
        "chunks": chunks,
        "chunk_stats": chunk_stats,
        "embed_stats": embed_stats,
        "embedding_service": embedding_service,
        "vector_store": vector_store,
    }
