from fastapi import FastAPI
from app.api.chat import router as chat_router
from app.api.ingest import router as ingest_router

app = FastAPI(
    title="Akash AI API"
)
app.include_router(chat_router)
app.include_router(ingest_router)

@app.get("/")
def home():
    return {
        "message": "Welcome to Akash AI"
    }

@app.get("/health")
def health():
    return {
        "status": "healthy"
    }