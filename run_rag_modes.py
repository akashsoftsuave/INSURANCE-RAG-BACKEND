"""Ask one question through the fixed and/or agentic RAG flow from the CLI.

    python run_rag_modes.py "what is the excess and what is the sum insured?"
    python run_rag_modes.py --mode agentic "list all exclusions"
    python run_rag_modes.py --mode auto --collection eval_acko_bike "who is the nominee?"

Writes to traces/manual_runs.jsonl by default, NOT traces/traces.jsonl --
that file holds the frozen 27-trace evaluation baseline.
"""

import argparse
import json

from app.services.rag_router import VALID_MODES, RAGRouter, classify
from app.services.trace_logger import TraceLogger


def show(result: dict) -> None:
    print(f"\n{'=' * 70}")
    print(f"mode={result['mode']} (requested={result['requested_mode']})  "
          f"rounds={result['rounds']}  latency={result['latency_ms']}ms  "
          f"tokens={result['tokens']['total']}  cost=${result['cost_usd']:.6f}")
    print(f"routing: {result['routing']['explanation']}")
    print(f"queries issued ({len(result['queries'])}):")
    for q in result["queries"]:
        print(f"  - {q}")
    print(f"sources: {json.dumps(result['sources'], ensure_ascii=False)}")
    print(f"trace_id: {result['trace_id']}")
    print(f"{'-' * 70}")
    print(result["answer"])
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--mode", default="both", choices=[*VALID_MODES, "both"],
                        help="'both' runs fixed and agentic back to back for comparison")
    parser.add_argument("--collection", default=None, help="override the Chroma collection")
    parser.add_argument("--trace-file", default="traces/manual_runs.jsonl")
    args = parser.parse_args()

    print(f"auto-mode classification: {classify(args.question)['explanation']}")

    router = RAGRouter(
        trace_logger=TraceLogger(args.trace_file),
        collection_name=args.collection,
    )

    modes = ["fixed", "agentic"] if args.mode == "both" else [args.mode]
    results = [router.ask(args.question, mode=m) for m in modes]
    for result in results:
        show(result)

    if len(results) == 2:
        f, a = results
        print(f"\nfixed vs agentic: latency {f['latency_ms']}ms -> {a['latency_ms']}ms "
              f"({a['latency_ms'] / max(f['latency_ms'], 1):.2f}x), "
              f"tokens {f['tokens']['total']} -> {a['tokens']['total']} "
              f"({a['tokens']['total'] / max(f['tokens']['total'], 1):.2f}x), "
              f"rounds {f['rounds']} -> {a['rounds']}")


if __name__ == "__main__":
    main()
