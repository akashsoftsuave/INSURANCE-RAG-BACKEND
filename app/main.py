from fastapi import FastAPI
from app.api.chat import router as chat_router
from app.api.ingest import router as ingest_router
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Akash AI API"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # Allow all origins
    allow_credentials=False,  # Must be False when using "*"
    allow_methods=["*"],      # Allow all HTTP methods
    allow_headers=["*"],      # Allow all headers
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