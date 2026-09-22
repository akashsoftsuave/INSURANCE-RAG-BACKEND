"""Week 8 — Module 4 — Practical Task Set D.

Outcome-vs-trajectory gap for the existing claims-triage agent, then one
mitigation for the top failure mode.

This reuses, and does not replace, what is already in the project:
  * the agent under test          -> app/claims_agent/agent.run_agent
  * its tools and fixed fixtures  -> app/claims_agent/tools.py, data.py
  * the OUTCOME evaluation        -> app/claims_agent/contract.grade + EXPECTED
The trajectory evaluation is layered on top, so both gradings score the
same ten claims from the same run and are directly comparable.

Usage (from repo root, venv active):
    python -m eval.trajectory_eval --phase before     # baseline
    python -m eval.trajectory_eval --phase after      # post-mitigation
    python -m eval.trajectory_eval --report           # compare + write report
    python -m eval.trajectory_eval --all              # all three, in order
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from app.core.config import settings
from app.claims_agent.agent import run_agent, DEFAULT_SEED
from app.claims_agent.budgets import BudgetConfig
from app.claims_agent.contract import grade
from eval.trajectory_cases import CASES, CASES_BY_ID
from eval.trajectory_metrics import (
    FAILURE_MODES,
    aggregate_step_efficiency,
    argument_validity,
    classify_failures,
    count_failure_modes,
    distribution,
    sequence_matches,
    step_efficiency,
    stepwise_tool_choice,
    tool_choice_accuracy,
    top_failure_mode,
    validate_call,
)

EVAL_DIR = Path(__file__).resolve().parent
BEFORE_PATH = EVAL_DIR / "trajectory_results_before.json"
AFTER_PATH = EVAL_DIR / "trajectory_results_after.json"
REPORT_PATH = EVAL_DIR / "trajectory_report.json"
REPORT_MD_PATH = EVAL_DIR / "trajectory_report.md"

# Evaluation budget. The Week-7 BudgetConfig defaults (6 iterations, 6000
# tokens, 20s wall clock) exist to DEMONSTRATE budget termination and are
# deliberately tight: a normal 4-iteration claim run costs ~5,400 tokens and,
# on a throttled provider, ~25s. Left at those defaults the trajectory
# evaluation measures provider latency instead of agent behaviour - the first
# baseline attempt scored 6/10 silent_give_up, every one of them a max_tokens
# or wall_clock cut-off mid-pipeline rather than a reasoning failure.
#
# This budget is headroom, not a guardrail: wide enough that no well-behaved
# run can reach it, so a budget termination in these results really is the
# agent giving up. Applied IDENTICALLY in the before and after phases, so it
# cannot flatter the mitigation.
EVAL_BUDGET = BudgetConfig(
    max_iters=8,
    max_tokens=30_000,
    max_cost=0.05,
    max_wall_clock_ms=180_000,
)

# Provider transport failures (429/5xx) are a property of the network, not of
# the agent's trajectory, so the harness retries the whole case a bounded
# number of times. Same policy in both phases.
#
# Note what is deliberately NOT retried: termination_reason "hallucinated_tool"
# — the provider rejecting a call to a tool the agent invented. That arrives as
# a 400 and looks transport-shaped, but it is the agent misbehaving, and
# retrying it would quietly delete a wrong_tool failure from the results.
TRANSIENT_RETRIES = 3
TRANSIENT_BACKOFF_S = 20

# The operating instruction the agent runs under for BOTH phases. See the long
# note above PLANNING_SYSTEM_PROMPT in app/claims_agent/agent.py: the Week-7
# prompt scripts the tool order in prose, so under it the agent scores 10/10
# outcome and 10/10 trajectory with a 0 pp gap and nothing left to mitigate.
# The planning prompt keeps every outcome rule and drops only the sentences
# that choose the tools, which is what makes a trajectory evaluation mean
# anything.
PROMPT_MODE = "planning"

# The 4-tool surface (get_claim / get_policy / check_exclusions /
# compute_payout). The Week-7 3-tool set folds the policy excess into the
# exclusion check, which makes "payout without checking exclusions"
# physically unreachable and the trajectory evaluation vacuous. Same set in
# both phases; only the tool DESCRIPTIONS differ when mitigation is on.
TOOL_SET = "task_d"


def _num_close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        return abs(float(a) - float(b)) < 0.01
    except (TypeError, ValueError):
        return False


# =========================================================================
# One case
# =========================================================================

def evaluate_case(case, result: dict) -> dict:
    """Score one agent run against one trajectory case.

    `result` is exactly what run_agent() returns, so this function is pure
    and can be re-run over the saved JSON without calling the model again.
    """
    trajectory = result.get("trajectory", [])
    actual_sequence = [s["tool"] for s in trajectory]
    output = result.get("output")

    # --- OUTCOME grading: the project's existing grader, unchanged -------
    outcome_pass, mismatches = grade(output, case.expected_outcome)

    # A second, deliberately narrower view of "was the answer right".
    #
    # grade() is the project's existing contract and stays the headline: it
    # checks all six graded fields, `excess` among them. But `excess` is a
    # REPORTED figure, not the decision — and Task Set D §4 asks specifically
    # for cases where "the final answer/payout is correct" on an invalid path.
    # An agent that skips compute_payout typically still gets the decision and
    # the money right and simply leaves `excess` null, because the payout tool
    # was the thing that would have echoed it back. That is exactly the shape
    # the task is hunting, so it is tracked separately rather than being
    # rounded away — and rather than loosening grade(), which would change the
    # outcome evaluation the whole comparison rests on.
    expected = case.expected_outcome
    payout_correct = bool(
        isinstance(output, dict)
        and output.get("status") == expected["status"]
        and bool(output.get("covered")) == bool(expected["covered"])
        and output.get("exclusion") == expected["exclusion"]
        and _num_close(output.get("gross_amount"), expected["gross_amount"])
        and _num_close(output.get("payable_amount"), expected["payable_amount"])
    )

    # --- argument validity, per call ------------------------------------
    per_call = []
    arg_issues: list[dict] = []
    for step in trajectory:
        issues = validate_call(step["tool"], step.get("arguments"), case)
        arg_issues.extend(issues)
        per_call.append({
            "order": step["order"],
            "tool": step["tool"],
            "arguments": step.get("arguments"),
            "valid": not issues,
            "issues": issues,
        })
    tool_calls_total = len(per_call)
    tool_calls_valid = sum(1 for c in per_call if c["valid"])

    # --- tool choice -----------------------------------------------------
    match = sequence_matches(actual_sequence, case.valid_paths)
    matched_steps, compared_steps = stepwise_tool_choice(actual_sequence, case.valid_paths)

    # --- failure taxonomy ------------------------------------------------
    modes = classify_failures(
        case, trajectory, result.get("termination_reason"), output, arg_issues
    )

    # --- trajectory verdict ----------------------------------------------
    # A trajectory passes when the sequence matches a valid path, every
    # argument refers to real data belonging to this claim, and the agent
    # actually finished. Equivalently: no failure mode fired.
    trajectory_pass = (
        match
        and tool_calls_valid == tool_calls_total
        and result.get("termination_reason") == "completed"
    )
    assert trajectory_pass == (not modes), (
        f"{case.case_id}: verdict/taxonomy disagree - pass={trajectory_pass} modes={modes}"
    )

    return {
        "case_id": case.case_id,
        "claim_id": case.claim_id,
        "variation": case.variation,
        "seed": result.get("seed", DEFAULT_SEED),

        # outcome
        "expected_outcome": case.expected_outcome,
        "actual_outcome": output,
        "outcome_pass": outcome_pass,
        "outcome_mismatches": mismatches,

        # trajectory
        "valid_paths": case.valid_paths,
        "allow_alternate_paths": case.allow_alternate_paths,
        "expected_tool_sequence": case.valid_paths[0],
        "actual_tool_sequence": actual_sequence,
        "sequence_match": match,
        "stepwise_tool_choice": {"matched": matched_steps, "compared": compared_steps},
        "trajectory_pass": trajectory_pass,
        "failure_modes": modes,

        # arguments
        "tool_calls_total": tool_calls_total,
        "tool_calls_valid": tool_calls_valid,
        "argument_checks": per_call,
        "argument_issues": arg_issues,

        # efficiency / cost
        "steps_taken": len(trajectory),
        "min_required_steps": case.min_required_steps,
        "step_efficiency": step_efficiency(len(trajectory), case.min_required_steps),
        "latency_ms": result.get("wall_clock_ms", 0),
        "tokens": result.get("tokens_total", 0),
        "cost_usd": result.get("cost_total", 0.0),
        "iterations": result.get("iterations", 0),
        "termination_reason": result.get("termination_reason"),

        # raw capture, kept so the run can be inspected later
        "trajectory": trajectory,
        "log": result.get("log", []),

        # right-answer-wrong-path flags. The strict one uses the project's
        # own grader; the payout-level one catches the case the task set
        # describes, where the decision and the money are right and only the
        # reported excess is missing because the payout tool was skipped.
        "payout_correct": payout_correct,
        "right_answer_wrong_path": bool(outcome_pass and not trajectory_pass),
        "payout_correct_wrong_path": bool(payout_correct and not trajectory_pass),
    }


# =========================================================================
# Whole run
# =========================================================================

def run_with_retry(case, mitigation: bool, seed: int, verbose: bool) -> dict:
    """Run one case, retrying only on a provider transport failure."""
    result = None
    for attempt in range(1, TRANSIENT_RETRIES + 1):
        result = run_agent(case.claim_id, budget_config=EVAL_BUDGET, seed=seed,
                           verbose=verbose, mitigation=mitigation,
                           prompt_mode=PROMPT_MODE, tool_set=TOOL_SET)
        if result["termination_reason"] != "model_error":
            return result
        if attempt < TRANSIENT_RETRIES:
            print(f"    [{case.case_id}] provider error on attempt {attempt}; "
                  f"retrying in {TRANSIENT_BACKOFF_S}s")
            time.sleep(TRANSIENT_BACKOFF_S)
    return result


def run_phase(phase: str, mitigation: bool, seed: int = DEFAULT_SEED,
              verbose: bool = False) -> dict:
    rows = []
    print(f"\n=== trajectory evaluation: phase={phase} "
          f"mitigation={'ON' if mitigation else 'OFF'} seed={seed} "
          f"prompt_mode={PROMPT_MODE} tool_set={TOOL_SET} ===")
    for case in CASES:
        result = run_with_retry(case, mitigation=mitigation, seed=seed, verbose=verbose)
        row = evaluate_case(case, result)
        rows.append(row)
        print(
            f"[{phase}] {row['case_id']} {row['claim_id']:<15} "
            f"outcome={'PASS' if row['outcome_pass'] else 'FAIL'} "
            f"trajectory={'PASS' if row['trajectory_pass'] else 'FAIL'} "
            f"steps={row['steps_taken']}/{row['min_required_steps']} "
            f"cost=${row['cost_usd']:.6f} latency={row['latency_ms']}ms "
            f"modes={','.join(row['failure_modes']) or '-'}"
        )
    return {"phase": phase, "mitigation": mitigation, "seed": seed,
            "prompt_mode": PROMPT_MODE, "tool_set": TOOL_SET,
            "budget": vars(EVAL_BUDGET),
            "rows": rows, "summary": summarize(rows)}


def rescore(payload: dict) -> dict:
    """Re-derive every metric from a saved run's raw traces, with no model
    calls. The saved rows carry the full trajectory, the final output and the
    termination reason, which is everything evaluate_case() needs — so a
    change to the metrics or the taxonomy can be re-applied to results that
    were collected earlier (Week 8 §11)."""
    rows = []
    for row in payload["rows"]:
        case = CASES_BY_ID[row["case_id"]]
        result = {
            "claim_id": row["claim_id"],
            "output": row["actual_outcome"],
            "termination_reason": row["termination_reason"],
            "trajectory": row["trajectory"],
            "seed": row.get("seed", DEFAULT_SEED),
            "iterations": row.get("iterations", 0),
            "tokens_total": row.get("tokens", 0),
            "cost_total": row.get("cost_usd", 0.0),
            "wall_clock_ms": row.get("latency_ms", 0),
            "log": row.get("log", []),
        }
        rows.append(evaluate_case(case, result))
    return {**payload, "rows": rows, "summary": summarize(rows)}


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    outcome_pass = sum(1 for r in rows if r["outcome_pass"])
    traj_pass = sum(1 for r in rows if r["trajectory_pass"])
    outcome_rate = outcome_pass / n if n else 0.0
    traj_rate = traj_pass / n if n else 0.0

    costs = [r["cost_usd"] for r in rows]
    latencies = [float(r["latency_ms"]) for r in rows]
    tokens = [float(r["tokens"]) for r in rows]

    matched = sum(r["stepwise_tool_choice"]["matched"] for r in rows)
    compared = sum(r["stepwise_tool_choice"]["compared"] for r in rows)

    counts = count_failure_modes(rows)
    top_mode, top_count = top_failure_mode(counts)

    return {
        "n_cases": n,
        "outcome_pass": outcome_pass,
        "outcome_pass_rate": outcome_rate,
        "trajectory_pass": traj_pass,
        "trajectory_pass_rate": traj_rate,
        # Reported in percentage points, per the rubric.
        "outcome_vs_trajectory_gap_pp": (outcome_rate - traj_rate) * 100,
        "tool_choice_accuracy": tool_choice_accuracy(rows),
        "tool_choice_accuracy_stepwise": (matched / compared) if compared else 0.0,
        "argument_validity": argument_validity(rows),
        "step_efficiency_aggregate": aggregate_step_efficiency(rows),
        "step_efficiency_per_case": {r["case_id"]: r["step_efficiency"] for r in rows},
        "cost_usd": distribution(costs),
        "latency_ms": distribution(latencies),
        "tokens": distribution(tokens),
        "failure_mode_counts": counts,
        "top_failure_mode": {"mode": top_mode, "count": top_count, "of": n},
        "right_answer_wrong_path_cases": [r["case_id"] for r in rows if r["right_answer_wrong_path"]],
        "payout_correct_wrong_path_cases": [r["case_id"] for r in rows if r["payout_correct_wrong_path"]],
        "payout_pass": sum(1 for r in rows if r["payout_correct"]),
    }


# =========================================================================
# Printing
# =========================================================================

def print_metrics_block(title: str, summary: dict) -> None:
    s = summary
    print(f"\n--- {title} ---")
    print(f"cases evaluated          : {s['n_cases']}")
    print(f"outcome pass rate        : {s['outcome_pass']}/{s['n_cases']} = {s['outcome_pass_rate']:.0%}")
    print(f"trajectory pass rate     : {s['trajectory_pass']}/{s['n_cases']} = {s['trajectory_pass_rate']:.0%}")
    print(f"OUTCOME-VS-TRAJECTORY GAP: {s['outcome_vs_trajectory_gap_pp']:.0f} percentage points")
    print(f"tool-choice accuracy     : {s['tool_choice_accuracy']:.0%} "
          f"(full-sequence match); {s['tool_choice_accuracy_stepwise']:.0%} step-wise")
    av = s["argument_validity"]
    print(f"argument validity rate   : {av['valid_calls']}/{av['total_calls']} = {av['rate']:.0%}")
    print(f"step efficiency (aggr.)  : {s['step_efficiency_aggregate']:.2f}x "
          f"(steps_taken / minimum_required_steps)")
    print("  per claim              : " + ", ".join(
        f"{cid}={v:.2f}" for cid, v in s["step_efficiency_per_case"].items()))
    c = s["cost_usd"]
    print(f"cost per claim           : p50 ${c['p50']:.6f}   max ${c['max']:.6f}   total ${c['total']:.6f}")
    print("  (token-based USD via app/claims_agent/pricing.py - real provider token counts)")
    lat = s["latency_ms"]
    print(f"latency per claim        : p50 {lat['p50']:.0f} ms   max {lat['max']:.0f} ms")
    tok = s["tokens"]
    print(f"tokens per claim         : p50 {tok['p50']:.0f}   max {tok['max']:.0f}")
    print("failure-mode counts      :")
    for mode in FAILURE_MODES:
        print(f"    {mode:<24} {s['failure_mode_counts'][mode]}/{s['n_cases']}")


def print_right_answer_wrong_path(rows: list[dict]) -> dict | None:
    """Prints the complete trace of one right-answer-wrong-path case."""
    strict = [r for r in rows if r["right_answer_wrong_path"]]
    payout = [r for r in rows if r["payout_correct_wrong_path"]]
    print("\n" + "=" * 72)
    print("RIGHT ANSWER, WRONG PATH")
    print("=" * 72)

    if strict:
        candidates = strict
        print(f"{len(candidates)} case(s) passed the full outcome grader with an "
              f"invalid trajectory: {', '.join(r['case_id'] for r in candidates)}")
    elif payout:
        candidates = payout
        print("No case passed the FULL outcome grader on an invalid trajectory "
              "in this run.")
        print(f"{len(payout)} case(s) did reach the correct DECISION AND PAYOUT "
              f"on an invalid trajectory: {', '.join(r['case_id'] for r in payout)}")
        print("  status, covered, exclusion, gross_amount and payable_amount all "
              "correct; only `excess` is missing,")
        print("  because the tool that would have echoed it back is the one that "
              "was skipped. This is the case")
        print("  Task Set D describes - the money is right and the path is not.")
    else:
        print("None found in this run: every case that reached the right payout "
              "also had a valid trajectory.")
        return None

    # Pick the most instructive one: prefer a skipped required step.
    ranked = sorted(candidates, key=lambda r: (
        0 if "skipped_required_step" in r["failure_modes"] else 1, r["case_id"]))
    r = ranked[0]
    case = CASES_BY_ID[r["case_id"]]

    print(f"\nCase ID             : {r['case_id']}  (claim {r['claim_id']}, {r['variation']})")
    print(f"Expected outcome    : {json.dumps(r['expected_outcome'])}")
    print(f"Actual outcome      : {json.dumps({k: r['actual_outcome'].get(k) for k in r['expected_outcome']})}")
    verdict = "PASS (full grader)" if r["right_answer_wrong_path"] else \
              "PASS on decision+payout, FAIL on the reported excess"
    print(f"Outcome verdict     : {verdict}  <- the payout is correct")
    if r["outcome_mismatches"]:
        print(f"Outcome mismatches  : {r['outcome_mismatches']}")
    print(f"Expected valid path : {r['valid_paths']}")
    print(f"Actual tool sequence: {r['actual_tool_sequence']}")
    print(f"Trajectory verdict  : FAIL")
    print(f"Failure modes       : {', '.join(r['failure_modes'])}")

    missing = sorted(case.required_tools - set(r["actual_tool_sequence"]))
    extra = sorted(set(r["actual_tool_sequence"]) - case.allowed_tools)
    print("\nWhy the trajectory is invalid:")
    if missing:
        print(f"  - SKIPPED REQUIRED STEP: {', '.join(missing)} appears in every valid")
        print(f"    path for this case and was never called.")
    if extra:
        print(f"  - WRONG TOOL: {', '.join(extra)} is not part of any valid path here.")
    if r["tool_calls_valid"] != r["tool_calls_total"]:
        for issue in r["argument_issues"]:
            print(f"  - {issue['kind'].upper()}: {issue['message']}")
    if not missing and not extra and r["tool_calls_valid"] == r["tool_calls_total"]:
        print(f"  - sequence {r['actual_tool_sequence']} matches none of the valid paths.")
    print(f"  - The final answer is still correct: {case.notes.strip()}")

    print("\nComplete recorded trace:")
    print(f"  steps_taken={r['steps_taken']}  min_required_steps={r['min_required_steps']}  "
          f"step_efficiency={r['step_efficiency']:.2f}")
    print(f"  iterations={r['iterations']}  tokens={r['tokens']}  cost=${r['cost_usd']:.6f}  "
          f"latency={r['latency_ms']}ms  termination={r['termination_reason']}")
    for step in r["trajectory"]:
        print(f"  [{step['order']}] tool={step['tool']}")
        print(f"      args   = {json.dumps(step['arguments'])}")
        print(f"      result = {json.dumps(step['result'])[:300]}")
        print(f"      latency_ms={step['latency_ms']}  error={step['error']}")
    print(f"  [final] agent output = {json.dumps(r['actual_outcome'])}")
    return r


def print_regression_table(before: dict, after: dict) -> list[dict]:
    b = before["summary"]["failure_mode_counts"]
    a = after["summary"]["failure_mode_counts"]
    print("\n" + "=" * 72)
    print("REGRESSION TABLE - every failure mode in the taxonomy")
    print("=" * 72)
    print(f"| {'Failure mode':<22} | {'Before':>6} | {'After':>5} | {'Delta':>5} |")
    print(f"|{'-' * 24}|{'-' * 8}|{'-' * 7}|{'-' * 7}|")
    table = []
    worse = []
    for mode in FAILURE_MODES:
        delta = a[mode] - b[mode]
        table.append({"mode": mode, "before": b[mode], "after": a[mode], "delta": delta})
        if delta > 0:
            worse.append(mode)
        shown = f"{delta:+d}" if delta else "0"
        print(f"| {mode:<22} | {b[mode]:>6} | {a[mode]:>5} | {shown:>5} |")

    print(f"\nModes checked: all {len(FAILURE_MODES)} in the taxonomy "
          f"({', '.join(FAILURE_MODES)}).")
    if worse:
        print("REGRESSION - these modes got WORSE after the mitigation:")
        for mode in worse:
            print(f"  - {mode}: {b[mode]} -> {a[mode]} (+{a[mode] - b[mode]})")
    else:
        print("No mode got worse. Every one of the eight modes above was "
              "re-counted after the mitigation and is flat or improved.")
    return table


def print_before_after(before: dict, after: dict, mitigation_meta: dict) -> dict:
    bs, as_ = before["summary"], after["summary"]
    top_mode = mitigation_meta["top_failure_mode"]
    b_count = bs["failure_mode_counts"][top_mode]
    a_count = as_["failure_mode_counts"][top_mode]

    print("\n" + "=" * 72)
    print("BEFORE -> AFTER")
    print("=" * 72)
    print(f"Top failure mode: {top_mode}")
    print(f"  {b_count} -> {a_count}  ({a_count - b_count:+d} cases, "
          f"{b_count}/{bs['n_cases']} -> {a_count}/{as_['n_cases']})")

    print("\nOutcome / trajectory:")
    print(f"  outcome pass rate    : {bs['outcome_pass_rate']:.0%} -> {as_['outcome_pass_rate']:.0%}")
    print(f"  trajectory pass rate : {bs['trajectory_pass_rate']:.0%} -> {as_['trajectory_pass_rate']:.0%}")
    print(f"  gap (pp)             : {bs['outcome_vs_trajectory_gap_pp']:.0f} -> {as_['outcome_vs_trajectory_gap_pp']:.0f}")

    d_cost_p50 = as_["cost_usd"]["p50"] - bs["cost_usd"]["p50"]
    d_cost_max = as_["cost_usd"]["max"] - bs["cost_usd"]["max"]
    d_lat_p50 = as_["latency_ms"]["p50"] - bs["latency_ms"]["p50"]
    d_lat_max = as_["latency_ms"]["max"] - bs["latency_ms"]["max"]
    d_tok_p50 = as_["tokens"]["p50"] - bs["tokens"]["p50"]
    d_tok_max = as_["tokens"]["max"] - bs["tokens"]["max"]

    print("\nPRICE OF THE MITIGATION (it is not free):")
    print(f"  tokens per claim : {d_tok_p50:+.0f} p50   {d_tok_max:+.0f} max   "
          f"({bs['tokens']['p50']:.0f} -> {as_['tokens']['p50']:.0f} p50)")
    print(f"  cost per claim   : {d_cost_p50:+.6f} USD p50   {d_cost_max:+.6f} USD max   "
          f"(${bs['cost_usd']['p50']:.6f} -> ${as_['cost_usd']['p50']:.6f} p50)")
    print(f"  latency per claim: {d_lat_p50:+.0f} ms p50   {d_lat_max:+.0f} ms max   "
          f"({bs['latency_ms']['p50']:.0f} -> {as_['latency_ms']['p50']:.0f} ms p50)")
    print(f"  steps per claim  : {bs['step_efficiency_aggregate']:.2f}x -> "
          f"{as_['step_efficiency_aggregate']:.2f}x step efficiency")

    return {
        "top_failure_mode": top_mode,
        "top_failure_before": b_count,
        "top_failure_after": a_count,
        "top_failure_delta": a_count - b_count,
        "price": {
            "tokens_per_claim_p50_delta": d_tok_p50,
            "tokens_per_claim_max_delta": d_tok_max,
            "cost_usd_per_claim_p50_delta": d_cost_p50,
            "cost_usd_per_claim_max_delta": d_cost_max,
            "latency_ms_per_claim_p50_delta": d_lat_p50,
            "latency_ms_per_claim_max_delta": d_lat_max,
        },
    }


# =========================================================================
# Persistence
# =========================================================================

def save(payload: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8")
    print(f"\nwrote {path}")
    return path


def load(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(
            f"{path} not found. Run `python -m eval.trajectory_eval --phase before` "
            f"and `--phase after` first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


# =========================================================================
# Report
# =========================================================================

def build_report(before: dict, after: dict) -> dict:
    from eval.mitigation import MITIGATION, description_diff

    bs = before["summary"]
    top_mode = bs["top_failure_mode"]["mode"]
    mitigation_meta = dict(MITIGATION, top_failure_mode=top_mode)

    print("\n" + "=" * 72)
    print("WEEK 8 - TASK SET D - FINAL EVALUATION REPORT")
    print("=" * 72)
    print(f"agent under test : app/claims_agent/agent.py::run_agent")
    print(f"cases            : eval/trajectory_cases.py (10 fixed claims)")
    print(f"outcome grader   : app/claims_agent/contract.py::grade vs data.py::EXPECTED (unchanged)")
    print(f"seed             : {before['seed']} (both phases)")

    print_metrics_block("BASELINE (before mitigation)", bs)
    raww = print_right_answer_wrong_path(before["rows"])

    print("\n" + "=" * 72)
    print("TOP FAILURE MODE")
    print("=" * 72)
    print(f"{top_mode}: {bs['top_failure_mode']['count']}/{bs['top_failure_mode']['of']} cases")
    print("\nFull ranking:")
    for mode, count in sorted(bs["failure_mode_counts"].items(),
                              key=lambda kv: (-kv[1], FAILURE_MODES.index(kv[0]))):
        print(f"  {mode:<24} {count}/{bs['n_cases']}")

    print("\n" + "=" * 72)
    print("MITIGATION (exactly one)")
    print("=" * 72)
    print(f"Targets           : {top_mode}")
    print(f"Chosen mitigation : {MITIGATION['name']}  (option {MITIGATION['option']} of the five allowed)")
    print(f"Code changed      : {MITIGATION['code_changed']}")
    print(f"Toggle            : {MITIGATION['toggle']}")
    print(f"Why this one      : {MITIGATION['rationale']}")
    print(f"Explicitly NOT done: {MITIGATION['not_done']}")

    print("\nExact diff — tool `description` fields, nothing else:")
    for row in description_diff():
        print(f"\n  ### {row['tool']}")
        print(f"  - {row['before']}")
        print(f"  + {row['after']}")

    print_metrics_block("AFTER MITIGATION", after["summary"])
    ba = print_before_after(before, after, mitigation_meta)
    table = print_regression_table(before, after)

    print("\n" + "=" * 72)
    print("OUTCOME REGRESSION CHECK")
    print("=" * 72)
    bo, ao = bs["outcome_pass_rate"], after["summary"]["outcome_pass_rate"]
    print(f"outcome pass rate {bo:.0%} -> {ao:.0%}  "
          f"({'no regression' if ao >= bo else 'REGRESSED'})")
    b_fail = {r["case_id"] for r in before["rows"] if not r["outcome_pass"]}
    a_fail = {r["case_id"] for r in after["rows"] if not r["outcome_pass"]}
    newly_broken = sorted(a_fail - b_fail)
    print(f"cases failing outcome before: {sorted(b_fail) or 'none'}")
    print(f"cases failing outcome after : {sorted(a_fail) or 'none'}")
    if newly_broken:
        print(f"NEWLY BROKEN BY THE MITIGATION: {newly_broken}")
    else:
        print("The mitigation broke no final answer that was previously correct.")

    print("\n" + "=" * 72)
    print("REMAINING FAILURE MODES (after mitigation)")
    print("=" * 72)
    remaining = {m: c for m, c in after["summary"]["failure_mode_counts"].items() if c}
    if remaining:
        for mode, count in sorted(remaining.items(), key=lambda kv: -kv[1]):
            cases = [r["case_id"] for r in after["rows"] if mode in r["failure_modes"]]
            print(f"  {mode:<24} {count}/10  {cases}")
    else:
        print("  none - every trajectory passed after the mitigation.")

    report = {
        "task": "Week 8 / Module 4 / Practical Task Set D",
        "agent_under_test": "app/claims_agent/agent.py::run_agent",
        "seed": before["seed"],
        "cases": [c.to_dict() for c in CASES],
        "baseline": bs,
        "right_answer_wrong_path": {
            "found": raww is not None,
            "all_cases": bs["right_answer_wrong_path_cases"],
            "payout_correct_wrong_path_cases": bs["payout_correct_wrong_path_cases"],
            "example": None if raww is None else {
                "case_id": raww["case_id"],
                "claim_id": raww["claim_id"],
                "expected_outcome": raww["expected_outcome"],
                "actual_outcome": raww["actual_outcome"],
                "outcome_pass": raww["outcome_pass"],
                "valid_paths": raww["valid_paths"],
                "actual_tool_sequence": raww["actual_tool_sequence"],
                "trajectory_pass": raww["trajectory_pass"],
                "failure_modes": raww["failure_modes"],
                "skipped_required_tools": sorted(
                    CASES_BY_ID[raww["case_id"]].required_tools - set(raww["actual_tool_sequence"])),
                "trajectory": raww["trajectory"],
            },
        },
        "top_failure_mode": bs["top_failure_mode"],
        "mitigation": dict(mitigation_meta, description_diff=description_diff()),
        "after": after["summary"],
        "before_after": ba,
        "regression_table": table,
        "outcome_regression": {
            "outcome_pass_rate_before": bo,
            "outcome_pass_rate_after": ao,
            "cases_failing_before": sorted(b_fail),
            "cases_failing_after": sorted(a_fail),
            "newly_broken": newly_broken,
        },
        "remaining_failure_modes": remaining,
    }
    return report


def write_report_md(report: dict) -> None:
    b, a = report["baseline"], report["after"]
    m = report["mitigation"]
    L: list[str] = []
    L.append("# Week 8 — Task Set D — Outcome-vs-Trajectory Gap\n")
    L.append(f"Agent under test: `{report['agent_under_test']}` · seed `{report['seed']}` · "
             f"10 fixed claims from `app/claims_agent/data.py`.\n")

    L.append("## Baseline\n")
    L.append("| Metric | Value |")
    L.append("|---|---|")
    L.append(f"| cases evaluated | {b['n_cases']} |")
    L.append(f"| outcome pass rate | {b['outcome_pass']}/{b['n_cases']} ({b['outcome_pass_rate']:.0%}) |")
    L.append(f"| trajectory pass rate | {b['trajectory_pass']}/{b['n_cases']} ({b['trajectory_pass_rate']:.0%}) |")
    L.append(f"| **outcome-vs-trajectory gap** | **{b['outcome_vs_trajectory_gap_pp']:.0f} percentage points** |")
    L.append(f"| tool-choice accuracy (full sequence) | {b['tool_choice_accuracy']:.0%} |")
    L.append(f"| tool-choice accuracy (step-wise) | {b['tool_choice_accuracy_stepwise']:.0%} |")
    L.append(f"| argument validity rate | {b['argument_validity']['valid_calls']}/{b['argument_validity']['total_calls']} ({b['argument_validity']['rate']:.0%}) |")
    L.append(f"| step efficiency (aggregate) | {b['step_efficiency_aggregate']:.2f}× |")
    L.append(f"| cost per claim p50 | ${b['cost_usd']['p50']:.6f} |")
    L.append(f"| cost per claim max | ${b['cost_usd']['max']:.6f} |")
    L.append(f"| latency p50 / max | {b['latency_ms']['p50']:.0f} ms / {b['latency_ms']['max']:.0f} ms |")
    L.append("")
    L.append("### Baseline failure-mode counts\n")
    L.append("| Failure mode | Cases |")
    L.append("|---|---:|")
    for mode in FAILURE_MODES:
        L.append(f"| {mode} | {b['failure_mode_counts'][mode]}/{b['n_cases']} |")
    L.append("")

    raww = report["right_answer_wrong_path"]
    L.append("## Right answer, wrong path\n")
    if raww["found"]:
        e = raww["example"]
        L.append(f"Cases: {', '.join(raww['all_cases'])}. Example below.\n")
        L.append(f"- **Case ID:** {e['case_id']} (claim {e['claim_id']})")
        L.append(f"- **Expected outcome:** `{json.dumps(e['expected_outcome'])}`")
        L.append(f"- **Actual outcome:** `{json.dumps({k: (e['actual_outcome'] or {}).get(k) for k in e['expected_outcome']})}` — **outcome PASS**")
        L.append(f"- **Expected valid path(s):** `{e['valid_paths']}`")
        L.append(f"- **Actual tool sequence:** `{e['actual_tool_sequence']}` — **trajectory FAIL**")
        L.append(f"- **Failure modes:** {', '.join(e['failure_modes'])}")
        L.append(f"- **Skipped required step(s):** {e['skipped_required_tools'] or 'none'}")
        L.append("")
    else:
        L.append("No case in this run produced a correct outcome on an invalid trajectory.\n")

    L.append("## Mitigation\n")
    L.append(f"- **Top failure mode:** `{report['top_failure_mode']['mode']}` "
             f"({report['top_failure_mode']['count']}/{report['top_failure_mode']['of']} cases)")
    L.append(f"- **Selected mitigation (exactly one):** {m['name']} — option {m['option']} of the five allowed")
    L.append(f"- **Code changed:** {m['code_changed']}")
    L.append(f"- **Toggle:** `{m['toggle']}`")
    L.append(f"- **Why:** {m['rationale']}")
    L.append(f"- **Not done:** {m['not_done']}\n")

    L.append("## After mitigation\n")
    L.append("| Metric | Before | After |")
    L.append("|---|---|---|")
    L.append(f"| outcome pass rate | {b['outcome_pass_rate']:.0%} | {a['outcome_pass_rate']:.0%} |")
    L.append(f"| trajectory pass rate | {b['trajectory_pass_rate']:.0%} | {a['trajectory_pass_rate']:.0%} |")
    L.append(f"| outcome-vs-trajectory gap | {b['outcome_vs_trajectory_gap_pp']:.0f} pp | {a['outcome_vs_trajectory_gap_pp']:.0f} pp |")
    L.append(f"| tool-choice accuracy | {b['tool_choice_accuracy']:.0%} | {a['tool_choice_accuracy']:.0%} |")
    L.append(f"| argument validity rate | {b['argument_validity']['rate']:.0%} | {a['argument_validity']['rate']:.0%} |")
    L.append(f"| step efficiency | {b['step_efficiency_aggregate']:.2f}× | {a['step_efficiency_aggregate']:.2f}× |")
    L.append(f"| cost p50 | ${b['cost_usd']['p50']:.6f} | ${a['cost_usd']['p50']:.6f} |")
    L.append(f"| cost max | ${b['cost_usd']['max']:.6f} | ${a['cost_usd']['max']:.6f} |")
    L.append("")

    ba = report["before_after"]
    p = ba["price"]
    L.append("## Before → After\n")
    L.append(f"**{ba['top_failure_mode']}: {ba['top_failure_before']} → {ba['top_failure_after']}** "
             f"({ba['top_failure_delta']:+d})\n")
    L.append("Mitigation price (it is not free):\n")
    L.append(f"- tokens per claim: {p['tokens_per_claim_p50_delta']:+.0f} p50, {p['tokens_per_claim_max_delta']:+.0f} max")
    L.append(f"- cost per claim: {p['cost_usd_per_claim_p50_delta']:+.6f} USD p50, {p['cost_usd_per_claim_max_delta']:+.6f} USD max")
    L.append(f"- latency per claim: {p['latency_ms_per_claim_p50_delta']:+.0f} ms p50, {p['latency_ms_per_claim_max_delta']:+.0f} ms max\n")

    L.append("## Regression — every failure mode in the taxonomy\n")
    L.append("| Failure mode | Before | After | Delta |")
    L.append("|---|---:|---:|---:|")
    for row in report["regression_table"]:
        shown = f"{row['delta']:+d}" if row["delta"] else "0"
        L.append(f"| {row['mode']} | {row['before']} | {row['after']} | {shown} |")
    worse = [r["mode"] for r in report["regression_table"] if r["delta"] > 0]
    L.append("")
    L.append(f"Modes checked: all {len(FAILURE_MODES)}.")
    L.append(f"{'Modes that got WORSE: ' + ', '.join(worse) if worse else 'No mode got worse.'}\n")

    orc = report["outcome_regression"]
    L.append("## Outcome regression check\n")
    L.append(f"Outcome pass rate {orc['outcome_pass_rate_before']:.0%} → {orc['outcome_pass_rate_after']:.0%}. "
             f"Newly broken: {orc['newly_broken'] or 'none'}.\n")

    L.append("## Remaining failure modes\n")
    if report["remaining_failure_modes"]:
        for mode, count in sorted(report["remaining_failure_modes"].items(), key=lambda kv: -kv[1]):
            L.append(f"- `{mode}`: {count}/10")
    else:
        L.append("- none")
    L.append("")

    REPORT_MD_PATH.write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {REPORT_MD_PATH}")


# =========================================================================
# CLI
# =========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["before", "after"],
                        help="run the 10 cases with the mitigation off (before) or on (after)")
    parser.add_argument("--rescore", action="store_true",
                        help="re-derive the metrics for both saved phases from "
                             "their stored traces, without calling the model")
    parser.add_argument("--report", action="store_true",
                        help="compare the saved before/after runs and write the report")
    parser.add_argument("--all", action="store_true",
                        help="run before, then after, then the report")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--api-key", choices=["primary", "secondary"], default="primary",
                        help="which Groq key from .env to use; 'secondary' is "
                             "GROQ_API_KEY_OLD, for when the primary key's daily "
                             "token allowance is spent")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if not (args.phase or args.report or args.all or args.rescore):
        parser.error("pass --phase before|after, --rescore, --report, or --all")

    if args.rescore:
        for path in (BEFORE_PATH, AFTER_PATH):
            if path.exists():
                payload = rescore(json.loads(path.read_text(encoding="utf-8")))
                save(payload, path)
                print_metrics_block(f"RESCORED {path.name}", payload["summary"])
            else:
                print(f"skipping {path.name} (not present yet)")
        return

    if args.api_key == "secondary":
        if not settings.GROQ_API_KEY_OLD:
            parser.error("--api-key secondary needs GROQ_API_KEY_OLD set in .env")
        # Swapped on the live settings object; run_agent builds its client per
        # call, so this takes effect for every case in the run. The key itself
        # is never logged or written into the results.
        settings.GROQ_API_KEY = settings.GROQ_API_KEY_OLD
        print("[eval] using the secondary Groq key (GROQ_API_KEY_OLD) from .env")

    if args.all or args.phase == "before":
        before = run_phase("before", mitigation=False, seed=args.seed, verbose=args.verbose)
        print_metrics_block("BASELINE (before mitigation)", before["summary"])
        print_right_answer_wrong_path(before["rows"])
        tm = before["summary"]["top_failure_mode"]
        print("\n" + "=" * 72)
        print("TOP FAILURE MODE")
        print("=" * 72)
        print(f"{tm['mode']}: {tm['count']}/{tm['of']} cases")
        save(before, BEFORE_PATH)

    if args.all or args.phase == "after":
        after = run_phase("after", mitigation=True, seed=args.seed, verbose=args.verbose)
        print_metrics_block("AFTER MITIGATION", after["summary"])
        save(after, AFTER_PATH)

    if args.all or args.report:
        before = load(BEFORE_PATH)
        after = load(AFTER_PATH)
        report = build_report(before, after)
        save(report, REPORT_PATH)
        write_report_md(report)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
