from pathlib import Path
import shutil

from fastapi import APIRouter, UploadFile, File, HTTPException

from app.services.ingestion_pipeline import run_ingestion_pipeline
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

    result = run_ingestion_pipeline(destination)
    pages = result["pages"]
    chunks = result["chunks"]

    print(f"[Ingest] {destination.name}: {len(pages)} pages -> {len(chunks)} chunks")
    for i, chunk in enumerate(chunks, 1):
        print(
            f"  [{i}] chunk_id={chunk.get('chunk_id')} page={chunk.get('page')} "
            f"section={chunk.get('section')} chars={len(chunk.get('text', ''))}"
        )

    return {
        "message": "Document processed successfully.",
        "filename": destination.name,
        "total_pages": len(pages),
        "total_chunks": len(chunks),
        "embedding_dimension": len(chunks[0]["embedding"]) if chunks else 0
    }