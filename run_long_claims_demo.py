"""Bonus (task section 14): rerun the race against 3 long (~30-turn)
adjuster-notes claims, with and without the sliding-window/summarization
context management from app/claims_agent/context_window.py, to check
whether summarization affects correctness.

    python run_long_claims_demo.py

For each of the 3 long claims, runs the agent and the workflow twice each:
  - "control"  : use_context_window=False -- full raw notes, no summary.
  - "windowed" : use_context_window=True  -- notes >12 turns get the last
                 6 turns kept verbatim and everything older summarized by
                 one fixed LLM call.

Grades every run against LONG_EXPECTED (computed from the full notes) and
reports where windowed disagrees with control -- that disagreement is
attributable to summarization, not to a business-logic difference, since
both runs use identical code and only differ in how much of the notes text
reaches cause-extraction.
"""

import json

from app.claims_agent.agent import run_agent
from app.claims_agent.workflow import run_workflow
from app.claims_agent.budgets import BudgetConfig
from app.claims_agent.long_claims import LONG_CLAIM_IDS, LONG_EXPECTED
from app.claims_agent.contract import grade
from app.claims_agent.runner import RESULTS_DIR

# ~30-turn notes are several times bigger than the short 10-claim dataset,
# and the tool result carrying them stays in the message history across
# every iteration -- the default 6000-token race budget is tuned for the
# short claims and isn't the thing being tested here, so it's widened for
# this run only. Iteration/cost/wall-clock ceilings are unchanged.
LONG_CLAIM_BUDGET = BudgetConfig(max_iters=6, max_tokens=20_000, max_cost=0.05, max_wall_clock_ms=45_000)


def run_condition(run_one, system_name: str, windowed: bool) -> list[dict]:
    rows = []
    for claim_id in LONG_CLAIM_IDS:
        kwargs = {"verbose": False, "use_context_window": windowed}
        if run_one is run_agent:
            kwargs["budget_config"] = LONG_CLAIM_BUDGET
        result = run_one(claim_id, **kwargs)
        passed, mismatches = grade(result["output"], LONG_EXPECTED[claim_id])
        rows.append({
            "claim_id": claim_id,
            "system": system_name,
            "condition": "windowed" if windowed else "control",
            "passed": passed,
            "mismatches": mismatches,
            "latency_ms": result["wall_clock_ms"],
            "iterations": result["iterations"],
            "tokens": result["tokens_total"],
            "cost": result["cost_total"],
            "termination_reason": result["termination_reason"],
            "output": result["output"],
        })
    return rows


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    passed = sum(1 for r in rows if r["passed"])
    return {
        "n": n,
        "pass_rate": passed / n if n else 0.0,
        "avg_latency_ms": sum(r["latency_ms"] for r in rows) / n if n else 0,
        "total_tokens": sum(r["tokens"] for r in rows),
        "total_cost": sum(r["cost"] for r in rows),
    }


def main():
    all_rows = []
    print("=== agent, control (full notes) ===")
    agent_control = run_condition(run_agent, "agent", windowed=False)
    for r in agent_control:
        print(f"  {r['claim_id']}: {'PASS' if r['passed'] else 'FAIL ' + str(r['mismatches'])} "
              f"tokens={r['tokens']} latency_ms={r['latency_ms']}")
    all_rows += agent_control

    print("\n=== agent, windowed (summarized) ===")
    agent_windowed = run_condition(run_agent, "agent", windowed=True)
    for r in agent_windowed:
        print(f"  {r['claim_id']}: {'PASS' if r['passed'] else 'FAIL ' + str(r['mismatches'])} "
              f"tokens={r['tokens']} latency_ms={r['latency_ms']}")
    all_rows += agent_windowed

    print("\n=== workflow, control (full notes) ===")
    workflow_control = run_condition(run_workflow, "workflow", windowed=False)
    for r in workflow_control:
        print(f"  {r['claim_id']}: {'PASS' if r['passed'] else 'FAIL ' + str(r['mismatches'])} "
              f"tokens={r['tokens']} latency_ms={r['latency_ms']}")
    all_rows += workflow_control

    print("\n=== workflow, windowed (summarized) ===")
    workflow_windowed = run_condition(run_workflow, "workflow", windowed=True)
    for r in workflow_windowed:
        print(f"  {r['claim_id']}: {'PASS' if r['passed'] else 'FAIL ' + str(r['mismatches'])} "
              f"tokens={r['tokens']} latency_ms={r['latency_ms']}")
    all_rows += workflow_windowed

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with (RESULTS_DIR / "long_claims_results.json").open("w", encoding="utf-8") as f:
        json.dump(all_rows, f, indent=2, ensure_ascii=False)

    print("\n=== SUMMARY ===")
    for label, rows in [
        ("agent control", agent_control), ("agent windowed", agent_windowed),
        ("workflow control", workflow_control), ("workflow windowed", workflow_windowed),
    ]:
        s = summarize(rows)
        print(f"{label:<20} pass_rate={s['pass_rate']:.0%}  total_tokens={s['total_tokens']:>6}  "
              f"total_cost=${s['total_cost']:.6f}  avg_latency_ms={s['avg_latency_ms']:.0f}")

    print("\n=== did summarization change correctness vs control? ===")
    any_flip = False
    for claim_id in LONG_CLAIM_IDS:
        for system_name, control_rows, windowed_rows in [
            ("agent", agent_control, agent_windowed),
            ("workflow", workflow_control, workflow_windowed),
        ]:
            c = next(r for r in control_rows if r["claim_id"] == claim_id)
            w = next(r for r in windowed_rows if r["claim_id"] == claim_id)
            if c["passed"] != w["passed"]:
                any_flip = True
                print(f"  {system_name} / {claim_id}: control passed={c['passed']} -> windowed passed={w['passed']} "
                      f"(windowed mismatches: {w['mismatches']})")
    if not any_flip:
        print("  No pass/fail flips between control and windowed runs for either system.")

    print(f"\nfull detail written to {RESULTS_DIR / 'long_claims_results.json'}")


if __name__ == "__main__":
    main()
