
"""
Usage (from repo root, with venv active):
    python -m eval.run_eval

To run a different predefined PDF + golden-question set, change
ACTIVE_DATASET below to one of the keys in eval/datasets.py
("shieldcare_full", "shieldcare_basic", "acko_bike").
"""

import json
import re

from app.services.ingestion_pipeline import run_ingestion_pipeline
from app.services.llm_service import LLMService
from app.services.retrieval_service import RetrievalService

from eval.datasets import DATASETS

ACTIVE_DATASET = "acko_bike"

TOP_K = 3


def compute_metrics(results: list, relevant_ids: set = None) -> dict:
    """Compute hit-rate@k, recall@k, and MRR metrics."""
    k = min(len(results), TOP_K)

    hit_at_k = 0
    if relevant_ids:
        retrieved_ids = {r.get("id") for r in results if r.get("id")}
        hit_at_k = len(retrieved_ids & relevant_ids) > 0

    recall_at_k = 0
    if relevant_ids and len(relevant_ids) > 0:
        retrieved_ids = {r.get("id") for r in results if r.get("id")}
        recall_at_k = len(retrieved_ids & relevant_ids) / len(relevant_ids)

    mrr = 0
    if relevant_ids:
        for i, r in enumerate(results, 1):
            if r.get("id") in relevant_ids:
                mrr = 1.0 / i
                break

    return {
        "hit_rate_at_k": hit_at_k,
        "recall_at_k": recall_at_k,
        "mrr": mrr
    }


def ingest(pdf_path, collection_name):
    result = run_ingestion_pipeline(pdf_path, collection_name=collection_name)
    chunks = result["chunks"]
    embedding_service = result["embedding_service"]
    vector_store = result["vector_store"]

    print(f"Ingested {len(chunks)} chunks from {pdf_path.name} into collection '{collection_name}'")
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
    metrics = compute_metrics(results, relevant_ids)
    context = "\n\n".join(r["document"] for r in results)
    answer = llm.generate(question=question, context=context)["raw_output"]
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
    if ACTIVE_DATASET not in DATASETS:
        raise ValueError(
            f"Unknown ACTIVE_DATASET {ACTIVE_DATASET!r}. "
            f"Choose one of: {', '.join(DATASETS)}"
        )

    dataset = DATASETS[ACTIVE_DATASET]
    pdf_path = dataset["pdf"]
    questions_path = dataset["questions"]
    report_path = dataset["report"]
    collection_name = dataset["collection"]

    report_path.parent.mkdir(parents=True, exist_ok=True)

    embedding_service, vector_store = ingest(pdf_path, collection_name)
    n_chunks = len(vector_store.collection.get()["ids"])

    retriever = RetrievalService()
    retriever.embedding_service = embedding_service  # reuse already-loaded model
    retriever.vector_store = vector_store
    llm = LLMService()

    questions = json.loads(questions_path.read_text())

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
        f"Dataset: `{ACTIVE_DATASET}` — corpus: `{pdf_path.name}` ({n_chunks} chunks), k={TOP_K}.\n"
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
    report_path.write_text(report)
    print(f"\nWrote report: {report_path}")
    print(f"hit-rate@{TOP_K}: {hit_rate:.0%}")


if __name__ == "__main__":
    main()
