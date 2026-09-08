
from __future__ import annotations

import json
import statistics
from pathlib import Path

from app.claims_agent.data import CLAIM_IDS, EXPECTED
from app.claims_agent.contract import grade

RESULTS_DIR = Path(__file__).resolve().parents[2] / "results"


def run_all(run_one, system_name: str, verbose: bool = False) -> list[dict]:
    """run_one(claim_id) -> the dict shape returned by run_agent()/run_workflow()."""
    rows = []
    for claim_id in CLAIM_IDS:
        result = run_one(claim_id, verbose=verbose)
        passed, mismatches = grade(result["output"], EXPECTED[claim_id])
        row = {
            "claim_id": claim_id,
            "system": system_name,
            "passed": passed,
            "mismatches": mismatches,
            "latency_ms": result["wall_clock_ms"],
            "iterations": result["iterations"],
            "tokens": result["tokens_total"],
            "cost": result["cost_total"],
            "termination_reason": result["termination_reason"],
            "output": result["output"],
            "log": result["log"],
        }
        rows.append(row)
        status = "PASS" if passed else f"FAIL {mismatches}"
        print(f"[{system_name}] {claim_id}: {status} | latency_ms={row['latency_ms']} "
              f"iterations={row['iterations']} tokens={row['tokens']} cost={row['cost']:.6f} "
              f"termination={row['termination_reason']}")
    return rows


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    passed = sum(1 for r in rows if r["passed"])
    latencies = sorted(r["latency_ms"] for r in rows)
    total_tokens = sum(r["tokens"] for r in rows)
    total_cost = sum(r["cost"] for r in rows)
    p50 = statistics.median(latencies) if latencies else 0
    return {
        "n": n,
        "pass_rate": passed / n if n else 0.0,
        "p50_latency_ms": p50,
        "total_tokens": total_tokens,
        "cost_per_claim": total_cost / n if n else 0.0,
        "total_cost": total_cost,
    }


def save_detail_json(rows: list[dict], filename: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / filename
    with path.open("w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    return path
