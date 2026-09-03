
import json
from pathlib import Path

from app.core.redaction import redact
from app.services.guardrail_service import GuardrailService
from app.services.ingestion_pipeline import run_ingestion_pipeline
from app.services.llm_service import LLMService
from app.services.retrieval_service import RetrievalService
from app.core.config import settings

TRACES_PATH = Path("traces/traces.jsonl")
PRE_FIX_PATH = Path("traces/pre_fix_archive_q2_q4_q7.jsonl")

# trace_id -> question, unredacted (needed to actually re-run retrieval+generation)
REGENERATE = {
    "199fd82b-99d4-4685-bd1d-5ba21ec3c1da": "What type of insurance policy do I have, and what does it cover?",  # Q2
    "b88b0456-7f2a-4449-a2ce-65544910b693": "How much premium did I pay for this policy, and is that the amount I would have to pay for a claim?",  # Q4
    "57e00158-da17-4636-ac8a-1ef363c9b445": "When exactly does my policy expire, and is the expiry date the same as the policy start date?",  # Q7
}


def load_traces():
    with TRACES_PATH.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, records: list):
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def build_trace_record(original: dict, question: str, retriever: RetrievalService, llm: LLMService, guard_svc: GuardrailService) -> dict:
    """Regenerate one trace's retrieval+generation using the CURRENT
    (post-fix) pipeline, preserving trace_id/question/timestamp-format so
    it slots back into traces.jsonl in the same shape as every untouched
    trace around it."""
    guard = guard_svc.check(question)
    post, pre = retriever.retrieve(question, return_pre_rerank=True)

    pre_rerank_candidates = [
        {
            "chunk_id": r["id"],
            "page": r.get("metadata", {}).get("page"),
            "section": r.get("metadata", {}).get("section"),
            "fusion_score": r.get("score"),
            "text_redacted": redact(r.get("document", "")),
        }
        for r in pre
    ]
    candidates = [
        {
            "chunk_id": r["id"],
            "page": r.get("metadata", {}).get("page"),
            "section": r.get("metadata", {}).get("section"),
            "fusion_score": r.get("score"),
            "rerank_score": r.get("rerank_score"),
            "text_redacted": redact(r.get("document", "")),
        }
        for r in post
    ]

    context = "\n\n".join(r["document"] for r in post)
    generation = llm.generate(question=question, context=context)
    answer = generation["raw_output"]

    return {
        "trace_id": original["trace_id"],
        "timestamp": original["timestamp"],
        "question_redacted": original["question_redacted"],
        "guardrail": guard,
        "retrieval": {
            "top_k": settings.TOP_K,
            "candidate_pool_size": len(pre),
            "pre_rerank_candidates": pre_rerank_candidates,
            "candidates": candidates,
        },
        "prompt_version": generation["prompt_version"],
        "model": {
            "name": generation["model"],
            "temperature": generation["temperature"],
            "provider": "groq",
        },
        "raw_output_redacted": redact(answer),
        "final_answer_redacted": redact(answer),
        "redaction": original["redaction"],
        "regenerated_after_fix": {
            "applied": True,
            "note": (
                "Regenerated via scripts/apply_task_set_d_updates.py after "
                "chunking (ChunkService oversized-unit overflow split, "
                "sentence-boundary safe) and retrieval "
                "(RetrievalService scenario-mismatch reranking penalty) "
                "fixes. Original pre-fix record archived verbatim in "
                "traces/pre_fix_archive_q2_q4_q7.jsonl. Reference label "
                "in eval/labels_30.json intentionally left unchanged."
            ),
        },
    }


def main():
    traces = load_traces()
    by_id = {t["trace_id"]: t for t in traces}

    missing = REGENERATE.keys() - by_id.keys()
    if missing:
        raise RuntimeError(f"expected regenerate trace_ids not found in traces.jsonl: {missing}")

    pre_fix_records = [by_id[tid] for tid in REGENERATE]

    write_jsonl(PRE_FIX_PATH, pre_fix_records)
    print(f"Archived {len(pre_fix_records)} pre-fix traces -> {PRE_FIX_PATH}")

    print("Re-ingesting acko_bike.pdf with current (fixed) pipeline...")
    result = run_ingestion_pipeline(Path("eval/data/acko_bike.pdf"), collection_name="eval_acko_bike_taskd_fix")
    retriever = RetrievalService()
    retriever.embedding_service = result["embedding_service"]
    retriever.vector_store = result["vector_store"]
    llm = LLMService()
    guard_svc = GuardrailService()

    new_records_by_id = {}
    for trace_id, question in REGENERATE.items():
        print(f"Regenerating trace {trace_id[:8]}... ({question!r})")
        new_records_by_id[trace_id] = build_trace_record(by_id[trace_id], question, retriever, llm, guard_svc)

    active_records = []
    for t in traces:
        tid = t["trace_id"]
        if tid in new_records_by_id:
            active_records.append(new_records_by_id[tid])
        else:
            active_records.append(t)

    write_jsonl(TRACES_PATH, active_records)
    print(f"Wrote {len(active_records)} active traces -> {TRACES_PATH}")


if __name__ == "__main__":
    main()
