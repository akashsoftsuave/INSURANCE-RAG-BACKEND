
"""
Usage (from repo root, with venv active):
    python -m eval.run_eval
"""

import json
import re
from pathlib import Path

from app.services.chunk_service import ChunkService
from app.services.embedding_service import EmbeddingService
from app.services.llm_service import LLMService
from app.services.pdf_loader import PDFLoader
from app.services.retrieval_service import RetrievalService
from app.services.vector_store import VectorStore

PDF_PATH = Path("documents/uploads/insurance.pdf")
QUESTIONS_PATH = Path("eval/questions.json")
REPORT_PATH = Path("eval/report.md")
TOP_K = 3


def ingest():
    pages = PDFLoader.extract_text(str(PDF_PATH))

    chunk_service = ChunkService(chunk_size=500, chunk_overlap=100)
    chunks = chunk_service.create_chunks(pages, document=PDF_PATH.name)

    embedding_service = EmbeddingService()
    chunks = embedding_service.generate_embeddings(chunks)

    vector_store = VectorStore()
    vector_store.clear_collection()
    vector_store.add_documents(chunks)

    print(f"Ingested {len(chunks)} chunks from {PDF_PATH.name}")
    return embedding_service, vector_store


def resolve_relevant_ids(vector_store, marker: str) -> set:
    data = vector_store.collection.get()
    matched = {
        doc_id
        for doc_id, text in zip(data["ids"], data["documents"])
        if marker.lower() in text.lower()
    }
    if not matched:
        print(f"[WARN] No chunks matched marker: {marker!r}")
    return matched


def run_config(retriever, llm, question: str, relevant_ids: set):
    results = retriever.retrieve(question)
    metrics = retriever.compute_metrics(results, relevant_ids)
    context = "\n\n".join(r["document"] for r in results)
    answer = llm.generate_answer(question=question, context=context)
    return {
        "results": results,
        "metrics": metrics,
        "answer": answer,
    }


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def label_failure(hit: bool, answer_ok: bool) -> str:
    if answer_ok:
        return "OK"
    if not hit:
        return "RETRIEVAL_FAILURE"
    return "GENERATION_FAILURE"


def main():
    embedding_service, vector_store = ingest()
    n_chunks = len(vector_store.collection.get()["ids"])

    retriever = RetrievalService()
    retriever.embedding_service = embedding_service  # reuse already-loaded model
    retriever.vector_store = vector_store
    llm = LLMService()

    questions = json.loads(QUESTIONS_PATH.read_text())

    rows = []
    for q in questions:
        relevant_ids = resolve_relevant_ids(vector_store, q["marker"])

        result = run_config(retriever, llm, q["question"], relevant_ids)

        hit = bool(result["metrics"]["hit_rate_at_k"])
        expected = normalize(q["expected_answer_contains"])
        answer_ok = expected in normalize(result["answer"])

        rows.append({
            "question": q["question"],
            "relevant_ids": sorted(relevant_ids),
            "hit": hit,
            "recall": result["metrics"]["recall_at_k"],
            "mrr": result["metrics"]["mrr"],
            "label": label_failure(hit, answer_ok),
            "answer": result["answer"],
            "retrieved": [(r["id"], r["metadata"].get("section")) for r in result["results"]],
        })

    n = len(rows)
    hit_rate = sum(r["hit"] for r in rows) / n
    mean_recall = sum(r["recall"] for r in rows) / n
    mean_mrr = sum(r["mrr"] for r in rows) / n

    failures = [r for r in rows if r["label"] != "OK"]

    lines = []
    lines.append("# Retrieval Evaluation Report\n")
    lines.append(
        "**Configuration under test:** current implementation "
        "(hybrid RRF + cross-encoder rerank `ms-marco-MiniLM-L-6-v2`). "
        f"Corpus: `{PDF_PATH.name}` ({n_chunks} chunks), k={TOP_K}.\n"
    )
    lines.append("## Headline numbers\n")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| hit-rate@{TOP_K} | {sum(r['hit'] for r in rows)}/{n} ({hit_rate:.0%}) |")
    lines.append(f"| mean recall@{TOP_K} | {mean_recall:.0%} |")
    lines.append(f"| mean MRR | {mean_mrr:.3f} |")
    lines.append("")
    lines.append(f"Failures: {len(failures)}/{n}.\n")

    lines.append("## Per-question results\n")
    lines.append("| # | Question | Hit | Label |")
    lines.append("|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        lines.append(
            f"| {i} | {r['question']} | {'✓' if r['hit'] else '✗'} | {r['label']} |"
        )
    lines.append("")

    lines.append("## Inspection view — failures (retrieval vs generation)\n")
    if not failures:
        lines.append("None — no failures on this question set.\n")
    for r in failures:
        lines.append(f"### \"{r['question']}\"")
        lines.append(f"- **Label:** {r['label']}")
        lines.append(f"- **Relevant chunk(s):** {r['relevant_ids']}")
        lines.append(f"- **Retrieved (id, section):** {r['retrieved']}")
        lines.append(f"- **Answer:** {r['answer']!r}")
        if r["label"] == "RETRIEVAL_FAILURE":
            lines.append(
                "  - The correct chunk was never in the retrieved candidate set — "
                "this is a candidate-generation failure, not a reranking/generation issue."
            )
        lines.append("")

    report = "\n".join(lines)
    REPORT_PATH.write_text(report)
    print(f"\nWrote report: {REPORT_PATH}")
    print(f"hit-rate@{TOP_K}: {hit_rate:.0%}")


if __name__ == "__main__":
    main()
