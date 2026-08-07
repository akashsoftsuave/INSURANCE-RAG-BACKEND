from pathlib import Path
import shutil

from fastapi import APIRouter, UploadFile, File, HTTPException

from app.services.pdf_loader import PDFLoader
from app.services.chunk_service import ChunkService
from app.services.embedding_service import EmbeddingService
from app.services.vector_store import VectorStore
from app.schemas.ingestSchemas import IngestResponse

router = APIRouter(
    prefix="/api/v1/ingest",
    tags=["Document Ingestion"]
)

UPLOAD_DIR = Path("documents/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/pdf", response_model=IngestResponse)
async def upload_pdf(file: UploadFile = File(...)):
    """
    Upload a PDF and prepare it for RAG.
    """

    # Validate uploaded file
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are allowed."
        )

    # Delete old PDF (single document project)
    for existing_pdf in UPLOAD_DIR.glob("*.pdf"):
        existing_pdf.unlink()

    # Save uploaded PDF
    destination = UPLOAD_DIR / "insurance.pdf"

    with open(destination, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # -------------------------
    # Step 1 : Extract Text
    # -------------------------

    pages = PDFLoader.extract_text(str(destination))

    # -------------------------
    # Step 2 : Chunk Text
    # -------------------------

    chunk_service = ChunkService(
        chunk_size=500,
        chunk_overlap=100
    )

    chunks = chunk_service.create_chunks(pages, document=destination.name)

    # -------------------------
    # Step 3 : Generate Embeddings
    # -------------------------

    embedding_service = EmbeddingService()

    chunks = embedding_service.generate_embeddings(chunks)

    # -------------------------
    # Step 3 : Store in Vector Store
    # -------------------------

    vector_store = VectorStore()

    vector_store.clear_collection()

    vector_store.add_documents(chunks)

    return {
        "message": "Document processed successfully.",
        "filename": destination.name,
        "total_pages": len(pages),
        "total_chunks": len(chunks),
        "embedding_dimension": len(chunks[0]["embedding"]) if chunks else 0
    }