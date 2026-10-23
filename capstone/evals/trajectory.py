"""Week-8 trajectory eval on the integrated agent: tool-choice accuracy, argument validity, step efficiency, and the
outcome-vs-trajectory gap. Pure functions over stored runs (no model calls) so every number re-derives from the JSON.

Valid paths (the order of steps 2-4 is free, lookup is first, referral is last):
    lookup_policy -> {check_coverage_in_force, search_policy_wording, get_limits in any order} -> refer_to_adjuster
    For a policy that was NOT in force on the loss date, the short path lookup -> check -> refer is also valid.

Outcome pass    = the deterministic OUTCOME assertions all pass (what the adjuster receives).
Trajectory pass = valid tool sequence AND every call argument valid (right loss date, right product, no error calls)
                  AND no forced referral AND no repeated tool.
Gap             = outcome pass rate - trajectory pass rate (percentage points): right answers reached by a wrong path.
"""
from __future__ import annotations

import itertools

OUTCOME_ASSERTS = ("referred", "no_verdict_language", "loss_date_correct", "in_force_checked", "citation_version",
                   "citation_format", "limits_three_numbers")

_MID = ["check_coverage_in_force", "search_policy_wording", "get_limits"]
VALID_PATHS = [["lookup_policy", *p, "refer_to_adjuster"] for p in itertools.permutations(_MID)]
SHORT_PATH = [["lookup_policy", "check_coverage_in_force", "refer_to_adjuster"]]


def valid_paths(case: dict) -> list[list[str]]:
    if case["oracle"].get("in_force") is False or case["oracle"].get("expect_unknown"):
        return VALID_PATHS + SHORT_PATH + [["lookup_policy", "refer_to_adjuster"]]
    return VALID_PATHS


def argument_problems(case: dict, run: dict) -> list[str]:
    o, probs = case["oracle"], []
    for s in run["trajectory"]:
        a, t = s["arguments"], s["tool"]
        if "error" in s["result"]:
            probs.append(f"{t}: tool returned error ({str(s['result']['error'])[:60]})")
        if "loss_date" in a and a["loss_date"] != o["loss_date"]:
            probs.append(f"{t}: loss_date={a['loss_date']!r}")
        if t in ("search_policy_wording", "get_limits") and o.get("product") and a.get("product") != o["product"]:
            probs.append(f"{t}: product={a.get('product')!r}")
        if t == "get_limits" and o.get("peril") and a.get("peril") != o["peril"]:
            probs.append(f"get_limits: peril={a.get('peril')!r}")
    return probs


def score_run(case: dict, run: dict, assertion_results: dict) -> dict:
    seq = run["tool_sequence"]
    paths = valid_paths(case)
    exact = seq in paths
    stepwise = [(i < len(p) and seq[i] == p[i]) for p in [max(paths, key=lambda p: sum(a == b for a, b in zip(p, seq)))] for i in range(len(seq))]
    dup = len(seq) - len(set(seq))
    probs = argument_problems(case, run)
    forced = run["referral"].get("forced_by_code", False)
    outcome = all(assertion_results[a]["passed"] for a in OUTCOME_ASSERTS if a in assertion_results)
    traj = exact and not probs and not forced and dup == 0
    optimum = min(len(p) for p in paths)
    return {"case_id": case["case_id"], "tool_sequence": seq, "exact_valid_path": exact,
            "stepwise_ok": sum(stepwise), "steps": len(seq), "argument_problems": probs, "duplicate_calls": dup,
            "forced_referral": forced, "outcome_pass": outcome, "trajectory_pass": traj,
            "right_answer_wrong_path": outcome and not traj, "wrong_answer_right_path": (not outcome) and traj,
            "step_efficiency": round(len(seq) / optimum, 2), "trace_id": run["trace_id"],
            "cost_usd": run["totals"]["cost_usd"], "latency_ms": run["totals"]["latency_ms"]}


def summarise(scores: list[dict]) -> dict:
    n = len(scores)
    pct = lambda k: round(100 * sum(1 for s in scores if s[k]) / n, 1)
    steps = sum(s["steps"] for s in scores)
    calls = sum(s["steps"] for s in scores)
    bad_calls = sum(len(s["argument_problems"]) for s in scores)
    ordered = sorted(s["cost_usd"] for s in scores)
    lat = sorted(s["latency_ms"] for s in scores)
    p = lambda xs, q: xs[min(len(xs) - 1, int(q * len(xs)))]
    return {
        "n": n, "outcome_pass_pct": pct("outcome_pass"), "trajectory_pass_pct": pct("trajectory_pass"),
        "gap_points": round(pct("outcome_pass") - pct("trajectory_pass"), 1),
        "right_answer_wrong_path": [s["case_id"] for s in scores if s["right_answer_wrong_path"]],
        "wrong_answer_right_path": [s["case_id"] for s in scores if s["wrong_answer_right_path"]],
        "tool_choice_accuracy_full_sequence_pct": pct("exact_valid_path"),
        "tool_choice_accuracy_stepwise_pct": round(100 * sum(s["stepwise_ok"] for s in scores) / steps, 1),
        "argument_validity_pct": round(100 * (calls - bad_calls) / calls, 1),
        "step_efficiency_mean": round(sum(s["step_efficiency"] for s in scores) / n, 2),
        "cost_per_claim_p50": ordered[len(ordered) // 2], "cost_per_claim_max": ordered[-1],
        "latency_ms_p50": lat[len(lat) // 2], "latency_ms_p95": p(lat, 0.95),
    }
