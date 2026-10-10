"""Week 11 — the live regression suite the drill's eval case was added to.

The Week-6 regression runner (eval/regression_runner.py) scores FROZEN
traces, so a prompt fix can never turn it red or green. This suite runs the
same Week-6 material LIVE through the real pipeline at a chosen prompt
version, so the whole suite — not just the new case — is re-run on every
change:

  1. Week-6 golden questions: eval/questions/{acko_bike,shieldcare_full,
     shieldcare_basic}.json, graded exactly like eval/run_eval.py (normalised
     substring of expected_answer_contains). Includes the deductible case.
  2. Week-6 deterministic checks (eval/deterministic_checks.py) on every
     answer — any FAIL fails the case.
  3. Every case file in eval/cases/*.json (Week 11 onwards), graded on the
     coverage verdict the answer gives (app/observability/indexers.py — the
     same classifier the request log indexes on) plus a must_mention regex.

    python -m eval.coverage_suite --prompt-version insurance-qa-v2 --out eval/results/w11_red.json
    python -m eval.coverage_suite --prompt-version insurance-qa-v3 --out eval/results/w11_green.json

Prints "SUITE <version>: <pass>/<total> PASS" — the pass count, both runs.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
from pathlib import Path

from groq import RateLimitError

from app.observability.indexers import classify_coverage_verdict
from app.services.guardrail_service import GuardrailService
from app.services.llm_service import PROMPTS, LLMService
from app.services.rag_service import RAGService
from app.services.retrieval_service import RetrievalService
from app.services.trace_logger import TraceLogger
from app.services.vector_store import VectorStore
from eval.datasets import DATASETS
from eval.deterministic_checks import FAIL as CHECK_FAIL, run_all_checks

CASES_DIR = Path("eval/cases")


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def load_suite() -> list[dict]:
    cases = []
    for key in ("acko_bike", "shieldcare_full", "shieldcare_basic"):
        ds = DATASETS[key]
        for i, q in enumerate(json.loads(ds["questions"].read_text(encoding="utf-8")), 1):
            cases.append({
                "id": f"W6-{key}-{i:02d}", "kind": "week6_golden", "collection": ds["collection"],
                "question": q["question"], "expected_answer_contains": q["expected_answer_contains"],
            })
    for path in sorted(CASES_DIR.glob("*.json")):
        for c in json.loads(path.read_text(encoding="utf-8"))["cases"]:
            cases.append({**c, "kind": "case_file", "file": str(path).replace("\\", "/")})
    return cases


def grade(case: dict, answer: str) -> tuple[bool, list[str]]:
    reasons = []
    for chk in run_all_checks(answer):
        if chk["status"] == CHECK_FAIL:
            reasons.append(f"week6_check:{chk['check']}: {chk['detail']}")
    if case["kind"] == "week6_golden":
        if normalize(case["expected_answer_contains"]) not in normalize(answer):
            reasons.append(f"expected_answer_contains {case['expected_answer_contains']!r} not in answer")
    else:
        verdict = classify_coverage_verdict(answer, "coverage_question")
        if verdict != case["expected_verdict"]:
            reasons.append(f"verdict={verdict}, expected {case['expected_verdict']}")
        if case.get("must_mention") and not re.search(case["must_mention"], answer or "", re.I):
            reasons.append(f"must_mention /{case['must_mention']}/ missing")
    return (not reasons), reasons


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt-version", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pause", type=float, default=6.0)
    ap.add_argument("--only", default=None, help="regex on case id")
    args = ap.parse_args()
    if args.prompt_version not in PROMPTS:
        sys.exit(f"unknown prompt version {args.prompt_version!r}; known: {sorted(PROMPTS)}")

    cases = load_suite()
    if args.only:
        cases = [c for c in cases if re.search(args.only, c["id"])]

    base = RetrievalService()
    llm, guard = LLMService(), GuardrailService()
    # The suite must not write into the production request log or the frozen
    # Week-5/6 trace file.
    sink = TraceLogger("week11_observability/logs/suite_legacy_traces.jsonl")
    services: dict[str, RAGService] = {}

    def service_for(collection: str) -> RAGService:
        if collection not in services:
            r = copy.copy(base)
            r.vector_store = VectorStore(collection_name=collection)
            services[collection] = RAGService(trace_logger=sink, retriever=r, llm=llm,
                                              guardrail=guard, enable_request_log=False)
        return services[collection]

    rows = []
    for i, case in enumerate(cases, 1):
        for attempt in range(6):
            try:
                result = service_for(case["collection"]).ask(
                    case["question"], user_id="eval-suite", prompt_version=args.prompt_version)
                break
            except RateLimitError:
                time.sleep(20)
        else:
            raise RuntimeError("rate limited on every retry")
        ok, reasons = grade(case, result["answer"])
        rows.append({"id": case["id"], "kind": case["kind"], "question": case["question"],
                     "pass": ok, "reasons": reasons, "answer": result["answer"],
                     "cost_usd": result["cost_usd"], "latency_ms": result["latency_ms"]})
        print(f"{'PASS' if ok else 'FAIL'}  {case['id']:22s} {case['question'][:60]:60s} "
              f"{'' if ok else '<- ' + '; '.join(reasons)}", flush=True)
        if i < len(cases):
            time.sleep(args.pause)

    passed = sum(r["pass"] for r in rows)
    by_kind = {}
    for r in rows:
        k = by_kind.setdefault(r["kind"], [0, 0])
        k[0] += r["pass"]; k[1] += 1
    print()
    for kind, (p, t) in by_kind.items():
        print(f"  {kind:14s} {p}/{t}")
    print(f"SUITE {args.prompt_version}: {passed}/{len(rows)} PASS")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({
        "prompt_version": args.prompt_version, "pass": passed, "total": len(rows),
        "by_kind": {k: {"pass": p, "total": t} for k, (p, t) in by_kind.items()},
        "rows": rows,
    }, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
