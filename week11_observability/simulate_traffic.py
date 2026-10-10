"""Generate a day of real adjuster traffic through the instrumented pipeline.

Every request is a REAL call: real retrieval over the Chroma collections,
a real Groq generation, real tokens, cost and latency in the request log.
Nothing in the log is hand-written.

The squadmate's plant is passed in with --plant: a JSON file holding
{"question", "user_id", "collection", "position"}. It is kept OUTSIDE the
repo so the person running the drill cannot read it, and this script writes
nothing to the log that marks the planted line — it is one more request.

    python -m week11_observability.simulate_traffic --schema week11-log-v1 \
        --plant C:/somewhere/outside/plant1.json
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import sys
import time

from app.observability.request_log import RequestLog
from app.services.guardrail_service import GuardrailService
from app.services.llm_service import LLMService
from app.services.rag_service import RAGService
from app.services.retrieval_service import RetrievalService
from app.services.trace_logger import TraceLogger
from app.services.vector_store import VectorStore

ACKO = "insurance_documents"          # the ACKO two-wheeler schedule (production collection)
SHIELD = "eval_shieldcare_full"       # the ShieldCare Plus health policy

USERS = ["ADJ-0101", "ADJ-0102", "ADJ-0103", "ADJ-0104",
         "ADJ-0105", "ADJ-0106", "ADJ-0107", "ADJ-0108"]

# Realistic adjuster questions. Some carry claimant PII on purpose — the log
# must show it redacted on write.
TRAFFIC = [
    (ACKO, "when does the bike policy expire"),
    (SHIELD, "what is the deductible for hospitalization"),
    (ACKO, "what is the total premium paid including GST"),
    (SHIELD, "are cosmetic procedures covered"),
    (ACKO, "who is the nominee on the bike policy"),
    (SHIELD, "is ambulance cover included and what is the limit"),
    (ACKO, "what is the limit for damage to third party property"),
    (SHIELD, "what is the status of claim CLM-2026-00437"),
    (ACKO, "Claimant Ravi Shankar (ph 9876543210) hit a parked car with his Splendor "
           "TN09DB0750. Is the damage to the other car covered?"),
    (SHIELD, "how long is the waiting period for maternity"),
    (ACKO, "how do I file a claim with ACKO"),
    (SHIELD, "is dental treatment covered and what is the co-pay"),
    (ACKO, "can someone with a learner's license ride the bike under this policy"),
    (SHIELD, "what is the outpatient consultation limit"),
    (ACKO, "what is the registration year of the bike"),
    (SHIELD, "how many days does the policyholder have to appeal a denied claim"),
    (ACKO, "the rider was doing food deliveries for money when he crashed - will the claim be paid?"),
    (SHIELD, "Policyholder Arun Kumar (arun.kumar@example.test) had surgery in Dubai. "
             "Is that treatment covered?"),
    (ACKO, "who was the previous policy insurer"),
    (SHIELD, "are mental health services covered"),
    (SHIELD, "what is the grace period for premium payment"),
    (ACKO, "what is the policy number for the Splendor"),
    (SHIELD, "which hospitals support cashless claims"),
    (SHIELD, "what is the base sum insured"),
    (ACKO, "is the owner-driver personal accident cover included and for how much"),
    (SHIELD, "what documents are needed to file a claim"),
    (SHIELD, "is AYUSH treatment covered"),
    (ACKO, "what is the engine CC and fuel type of the bike"),
]


def build_services(schema: str, log_path: str | None, legacy_path: str) -> dict[str, RAGService]:
    # One RetrievalService loads the embedding model and the cross-encoder;
    # the second collection reuses both and only swaps the vector store.
    base = RetrievalService(collection_name=ACKO)
    other = copy.copy(base)
    other.vector_store = VectorStore(collection_name=SHIELD)
    llm, guard = LLMService(), GuardrailService()
    request_log = RequestLog(log_path, schema=schema)
    legacy = TraceLogger(legacy_path)
    return {
        ACKO: RAGService(trace_logger=legacy, retriever=base, llm=llm, guardrail=guard, request_log=request_log),
        SHIELD: RAGService(trace_logger=legacy, retriever=other, llm=llm, guardrail=guard, request_log=request_log),
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", default="week11-log-v2", choices=["week11-log-v1", "week11-log-v2"])
    ap.add_argument("--log", default=None, help="request log path (default: settings.REQUEST_LOG_PATH)")
    ap.add_argument("--legacy-trace", default="week11_observability/logs/legacy_traces.jsonl",
                    help="legacy Week-5 trace sink; it stores raw chunk text, so it is gitignored")
    ap.add_argument("--plant", default=None, help="squadmate plant spec (JSON), kept outside the repo")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--n", type=int, default=len(TRAFFIC))
    ap.add_argument("--pause", type=float, default=7.0,
                    help="seconds between requests; the key is capped at 8,000 tokens/minute")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    plan = [(col, q, rng.choice(USERS)) for col, q in TRAFFIC[: args.n]]
    if args.plant:
        spec = json.loads(open(args.plant, encoding="utf-8").read())
        pos = spec.get("position", rng.randrange(len(plan) + 1))
        # A plant may pin a prompt version (e.g. the pre-fix stable one) so
        # the bad answer is reproducible while the fix is only in canary.
        plan.insert(pos, (spec["collection"], spec["question"], spec["user_id"], spec.get("prompt_version")))

    services = build_services(args.schema, args.log, args.legacy_trace)
    for i, (col, question, user, *pin) in enumerate(plan, 1):
        result = services[col].ask(question, user_id=user, channel="adjuster_console",
                                   prompt_version=(pin[0] if pin else None))
        print(f"[{i:02d}/{len(plan)}] {user} {col:22s} {result['prompt_version']} "
              f"{result['latency_ms']:5d}ms ${result['cost_usd']:.6f}", flush=True)
        if i < len(plan):
            time.sleep(args.pause)


if __name__ == "__main__":
    main()
