"""
Replays one trace using only what's recorded in traces/traces.jsonl, and
writes original-vs-replayed evidence to analysis/replay_<trace_id>.md.

Honesty check baked into the output, not left to be discovered later:
the trace stores the RETRIEVED CONTEXT WITH IDS/NAMES REDACTED (that's the
whole point of redact-before-write). So if the original answer quoted a
literal policy/claim number or name, the replay CANNOT reproduce that exact
value — only the structure/behavior of the answer. That gap is reported in
the output rather than papered over; it's the kind of thing requirement 1
asks you to find and say out loud.

Usage (from repo root, with venv active and .env configured):
    python -m scripts.replay_trace --seed 20260824 --pick-random
    python -m scripts.replay_trace --trace-id <uuid>
"""

import argparse
import random
from pathlib import Path

from app.services.llm_service import LLMService, PROMPT_VERSION
from app.services.trace_logger import TraceLogger

OUT_DIR = Path("analysis")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-id", default=None)
    parser.add_argument("--pick-random", action="store_true",
                         help="Pick one trace_id at random using --seed instead of naming one.")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    tracer = TraceLogger()
    records = tracer.read_all()
    if not records:
        raise SystemExit("No traces found. Run scripts/generate_traces.py first.")

    if args.pick_random:
        if args.seed is None:
            raise SystemExit("--pick-random requires --seed (paste the seed into notes.md).")
        rng = random.Random(args.seed)
        record = rng.choice(records)
    elif args.trace_id:
        record = tracer.get(args.trace_id)
        if record is None:
            raise SystemExit(f"trace_id {args.trace_id} not found.")
    else:
        raise SystemExit("Pass --trace-id <id> or --pick-random --seed <n>.")

    trace_id = record["trace_id"]
    missing_fields = []

    prompt_version = record.get("prompt_version")
    if prompt_version is None:
        missing_fields.append("prompt_version (guardrail-blocked trace — no LLM call happened)")
    elif prompt_version != PROMPT_VERSION:
        missing_fields.append(
            f"prompt_version mismatch: trace was built on {prompt_version!r}, "
            f"current code is {PROMPT_VERSION!r} — template may have drifted since this trace."
        )

    model_info = record.get("model")
    candidates = record.get("retrieval", {}).get("candidates", [])
    context_redacted = "\n\n".join(c["text_redacted"] for c in candidates)

    replayed_output = None
    if model_info and prompt_version:
        llm = LLMService()
        question = record["question_redacted"]  # only the redacted form was stored
        generation = llm.generate(question=question, context=context_redacted)
        replayed_output = generation["raw_output"]
    else:
        missing_fields.append("no model/context recorded — nothing to replay (guardrail short-circuited)")

    lines = []
    lines.append(f"# Replay evidence — trace {trace_id}\n")
    lines.append(f"- prompt_version recorded: `{prompt_version}`")
    lines.append(f"- model recorded: `{model_info}`")
    lines.append(f"- retrieved chunk_ids + scores: "
                 f"{[(c['chunk_id'], c.get('fusion_score'), c.get('rerank_score')) for c in candidates]}")
    lines.append("")
    lines.append("## Original (redacted, as stored)")
    lines.append(f"> {record.get('raw_output_redacted')}")
    lines.append("")
    lines.append("## Replayed (redacted, freshly generated from the trace)")
    lines.append(f"> {replayed_output}")
    lines.append("")
    lines.append("## Fields that could not be reconstructed / notes")
    if missing_fields:
        for m in missing_fields:
            lines.append(f"- {m}")
    lines.append(
        "- The context fed back into the model is the REDACTED chunk text (IDs/names masked). "
        "If the original answer quoted an exact policy/claim number, the replay structurally "
        "matches but cannot reproduce that literal value — that's a direct consequence of "
        "redacting before write, not a bug."
    )
    lines.append(
        "- Groq API calls are temperature=0 but not guaranteed bit-for-bit deterministic across "
        "calls/model revisions — treat small wording differences as expected, not a replay failure."
    )

    report = "\n".join(lines)
    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / f"replay_{trace_id}.md"
    out_path.write_text(report, encoding="utf-8")

    print(report)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
