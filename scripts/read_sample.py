"""
Pretty-prints each trace in a sample file, one at a time, for open-coding.

This only displays traces — it does not write sentences for you. Requirement
3 is that you personally read each one and write one honest sentence about
what you SAW (not a category, not a fix). Paste your sentences into
notes.md next to each trace_id as you go.

Usage (from repo root, with venv active):
    python -m scripts.read_sample analysis/sample_20.json
"""

import json
import sys
from pathlib import Path

from app.services.trace_logger import TraceLogger


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m scripts.read_sample <path-to-sample.json>")

    sample = json.loads(Path(sys.argv[1]).read_text())
    tracer = TraceLogger()
    records = {r["trace_id"]: r for r in tracer.read_all()}

    for i, trace_id in enumerate(sample["trace_ids"], 1):
        r = records.get(trace_id)
        print("=" * 80)
        print(f"[{i}/{len(sample['trace_ids'])}] trace_id={trace_id}")
        if not r:
            print("  !! trace_id not found in traces/traces.jsonl")
            continue
        print(f"  timestamp:        {r.get('timestamp')}")
        print(f"  question:         {r.get('question_redacted')}")
        print(f"  guardrail:        {r.get('guardrail')}")
        candidates = r.get("retrieval", {}).get("candidates", [])
        print(f"  retrieved ({len(candidates)}):")
        for c in candidates:
            print(f"    - chunk_id={c['chunk_id']} section={c.get('section')!r} "
                  f"page={c.get('page')} fusion={c.get('fusion_score'):.4f} "
                  f"rerank={c.get('rerank_score')}")
            print(f"      text: {c['text_redacted'][:200]!r}")
        print(f"  prompt_version:   {r.get('prompt_version')}")
        print(f"  model:            {r.get('model')}")
        print(f"  answer:           {r.get('final_answer_redacted')}")
        print()
        print("  >>> your one-sentence observation: ____________________")
        print()


if __name__ == "__main__":
    main()
