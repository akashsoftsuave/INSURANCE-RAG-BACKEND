"""Loss-date-aware wording retrieval, layered on the Week 3-4 RetrievalService.

Two modes, because the before/after is the point of the capstone:

  naive     product filter only. Whatever ranks best wins, whatever its date.
            This is the behaviour that cited the 2025 flood wording for a 2024 loss.
  in_force  product + effective_from <= loss_date <= effective_to, enforced
            (1) in the vector + BM25 queries via a metadata `where`, and
            (2) AGAIN in code on the returned rows, so a bug or a missing
            metadata field fails closed instead of leaking a wrong version.

The date filter is code, not a hint in a prompt.
"""
from __future__ import annotations

import contextlib
import sys
import time
from functools import lru_cache

from capstone import corpus
from capstone.config import CHROMA_PATH, COLLECTION, ymd_int


def clause_url(product: str, clause: str, as_of: str) -> str:
    return f"/api/v1/capstone/clause/{product}/{clause}?as_of={as_of}"


def _d(n: int) -> str:
    return "open" if n >= 99990000 else f"{n // 10000}-{n // 100 % 100:02d}-{n % 100:02d}"


def format_citation(c: dict, loss_date: str) -> str:
    ver = c["endorsement_version"]
    if ver == "BASE":
        return f"[{c['product'].upper()} wording cl.{c['clause']} (base wording, all dates) | loss date {loss_date}]"
    return (f"[{c['product'].upper()} cl.{c['clause']} | {c['endorsement_id']} ({ver} endorsement set, in force "
            f"{_d(c['effective_from'])} to {_d(c['effective_to'])}) | resolved for loss date {loss_date}]")


def ingest(reset: bool = True) -> dict:
    from app.services.embedding_service import EmbeddingService
    from app.services.vector_store import VectorStore
    vs = VectorStore(chroma_path=CHROMA_PATH, collection_name=COLLECTION)
    if reset:
        vs.clear_collection()
    chunks = corpus.load("wording_clauses")
    emb = EmbeddingService()
    texts = [f"{c['title']}. {c['text']}" for c in chunks]
    vecs = emb.model.encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False)
    vs.collection.add(
        ids=[c["chunk_id"] for c in chunks],
        documents=texts,
        embeddings=[v.tolist() for v in vecs],
        metadatas=[{"page": c["clause"], "section": c["title"], "product": c["product"], "clause": c["clause"],
                    "endorsement_id": c["endorsement_id"] or "", "endorsement_version": c["endorsement_version"],
                    "effective_from": c["effective_from"], "effective_to": c["effective_to"]} for c in chunks],
    )
    return {"chunks": len(chunks), "collection": COLLECTION}


@lru_cache(maxsize=1)
def _service():
    # The Week 3-4 class, unchanged, pointed at the capstone collection/path.
    from app.services.retrieval_service import RetrievalService
    from app.services.vector_store import VectorStore
    svc = RetrievalService(collection_name=COLLECTION)
    svc.vector_store = VectorStore(chroma_path=CHROMA_PATH, collection_name=COLLECTION)
    return svc


def retrieve_wording(query: str, product: str, loss_date: str, mode: str = "in_force",
                     top_k: int = 3) -> dict:
    """Returns {citations, mode, loss_date, timings_ms, date_filter_violations}."""
    if mode not in ("in_force", "naive"):
        raise ValueError(f"mode must be 'in_force' or 'naive', got {mode!r}")
    if product not in ("motor", "household"):
        raise ValueError(f"product must be 'motor' or 'household', got {product!r}")
    n = ymd_int(loss_date)  # raises on a malformed date: fail loudly, never fall back to 'latest'
    svc = _service()
    if mode == "in_force":
        where = {"$and": [{"product": product}, {"effective_from": {"$lte": n}}, {"effective_to": {"$gte": n}}]}
    else:
        where = {"product": product}
    timings: dict = {}
    t0 = time.monotonic()
    with contextlib.redirect_stdout(sys.stderr):   # stdout is the MCP protocol channel when this runs in the server
        rows = svc.retrieve(query, timings=timings, where=where)
    violations = 0
    if mode == "in_force":
        kept = []
        for r in rows:
            m = r["metadata"]
            if not ("effective_from" in m and "effective_to" in m and m["effective_from"] <= n <= m["effective_to"]):
                violations += 1  # fail closed
                continue
            kept.append(r)
        rows = kept
    citations = []
    for r in rows[:top_k]:
        m = r["metadata"]
        doc = r["document"]
        text = doc.split(". ", 1)[1] if doc.startswith(m["section"]) and ". " in doc else doc
        c = {"chunk_id": r["id"], "product": m["product"], "clause": m["clause"], "title": m["section"],
             "endorsement_id": m["endorsement_id"] or None, "endorsement_version": m["endorsement_version"],
             "effective_from": m["effective_from"], "effective_to": m["effective_to"],
             "text": text, "score": round(r.get("rerank_score", r.get("score", 0.0)), 4)}
        c["citation"] = format_citation(c, loss_date)
        c["url"] = clause_url(c["product"], c["clause"], loss_date)
        citations.append(c)
    return {"mode": mode, "loss_date": loss_date, "product": product, "citations": citations,
            "date_filter_violations": violations,
            "timings_ms": {**timings, "total_ms": round((time.monotonic() - t0) * 1000, 1)}}


def clause_in_force(product: str, clause: str, as_of: str) -> list[dict]:
    """Deterministic clause lookup (backs the clickable citation URL): rows for this clause in force on as_of."""
    n = ymd_int(as_of)
    return [c for c in corpus.load("wording_clauses")
            if c["product"] == product and c["clause"] == clause and c["effective_from"] <= n <= c["effective_to"]]
