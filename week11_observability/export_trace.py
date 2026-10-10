"""Export one request-log line as trace.json (the drill's evidence).

    python -m week11_observability.export_trace <trace_id-prefix> \
        --out week11_observability/deliverables/trace.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from week11_observability.logq import load


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("trace_id")
    ap.add_argument("--log", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    rec = next((r for r in load(a.log) if r["trace_id"].startswith(a.trace_id)), None)
    if rec is None:
        sys.exit(f"no trace starting with {a.trace_id}")

    # A flat per-span summary on top, the untouched log line underneath —
    # the summary is for reading, the raw line is the evidence.
    summary = {
        "trace_id": rec["trace_id"],
        "ts": rec["ts"],
        "user_id": rec["user_id"],
        "prompt_version": rec["prompt_version"],
        "model": rec["model"],
        "input_type": rec["input_type"],
        "retrieved_context_ids": [c["chunk_id"] for c in rec["context_ids"]],
        "spans": [
            {"span": s["name"], "stage": s["stage"], "latency_ms": s["latency_ms"],
             "tokens_prompt": s["tokens"]["prompt"], "tokens_completion": s["tokens"]["completion"],
             "cost_usd": s["cost_usd"]}
            for s in rec["spans"]
        ],
        "totals": rec["totals"],
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"summary": summary, "log_line": rec}, indent=2, ensure_ascii=False),
                           encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
