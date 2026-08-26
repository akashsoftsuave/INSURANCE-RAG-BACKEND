"""
Draws a seeded random sample of trace_ids from traces/traces.jsonl.

The seed is printed and written out alongside the sample — paste both into
notes.md so the sample is provable, not just asserted.

Usage (from repo root, with venv active):
    python -m scripts.seeded_sample --seed 20260824 --n 20
    python -m scripts.seeded_sample --seed 20260824 --n 10 --exclude-file analysis/sample_20.json
"""

import argparse
import json
import random
from pathlib import Path

from app.services.trace_logger import TraceLogger

OUT_DIR = Path("analysis")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True,
                         help="RNG seed — pick once, paste into notes.md, don't reroll to fish for a nicer sample.")
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--out", default=None,
                         help="Output filename under analysis/ (default: sample_<n>.json)")
    parser.add_argument("--exclude-file", default=None,
                         help="A previous sample_*.json to exclude from the draw (for the bonus 10-more-from-demo-set task).")
    args = parser.parse_args()

    tracer = TraceLogger()
    records = tracer.read_all()
    all_ids = [r["trace_id"] for r in records]

    if args.exclude_file:
        excluded = set(json.loads(Path(args.exclude_file).read_text())["trace_ids"])
        all_ids = [t for t in all_ids if t not in excluded]

    if len(all_ids) < args.n:
        raise SystemExit(
            f"Only {len(all_ids)} traces available, need {args.n}. "
            f"Run scripts/generate_traces.py first (or run it again for more volume)."
        )

    rng = random.Random(args.seed)
    sample = rng.sample(all_ids, args.n)

    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / (args.out or f"sample_{args.n}.json")
    out_path.write_text(json.dumps({
        "seed": args.seed,
        "n": args.n,
        "population_size": len(all_ids),
        "trace_ids": sample,
    }, indent=2))

    print(f"seed={args.seed}  population={len(all_ids)}  sampled={len(sample)}")
    print(f"Wrote {out_path}")
    for tid in sample:
        print(tid)


if __name__ == "__main__":
    main()
