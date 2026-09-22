"""Week 8 — Task Set D: trajectory metrics and the failure taxonomy.

Pure functions over a recorded trajectory. No model calls, no I/O — so the
same trajectory always scores the same way, and every number in the report
can be re-derived from the saved JSON.

The four required metrics:
  * tool-choice accuracy   -> sequence_matches() / tool_choice_accuracy()
  * argument validity rate -> validate_call() / argument_validity()
  * step efficiency        -> step_efficiency()
  * cost per claim         -> percentile()/p50 and max over cost_usd

Failure taxonomy (identical before and after the mitigation):
"""

from __future__ import annotations

import json

from app.claims_agent.data import CLAIMS, POLICIES
from app.claims_agent.tools import CAUSE_ENUM, CLAIM_STATUS_ENUM

# --- failure taxonomy ----------------------------------------------------
WRONG_TOOL = "wrong_tool"
INVALID_ARGUMENT = "invalid_argument"
UNNECESSARY_STEPS = "unnecessary_steps"
LOOP = "loop"
MADE_UP_INPUT = "made_up_input"
SILENT_GIVE_UP = "silent_give_up"
SKIPPED_REQUIRED_STEP = "skipped_required_step"
OTHER = "other"

FAILURE_MODES = [
    WRONG_TOOL,
    INVALID_ARGUMENT,
    UNNECESSARY_STEPS,
    LOOP,
    MADE_UP_INPUT,
    SILENT_GIVE_UP,
    SKIPPED_REQUIRED_STEP,
    OTHER,
]

# Termination reasons that mean the agent stopped without delivering a
# structured answer -> silent give-up.
GIVE_UP_REASONS = {
    "max_iterations", "max_tokens", "max_cost", "wall_clock",
    "model_error", "invalid_output_json", "invalid_output_schema",
    # The agent invented a tool, the provider refused the call, and the agent
    # produced no answer. Both wrong_tool and silent_give_up are true of it.
    "hallucinated_tool",
}

# Every excess figure and every sub-limit figure that exists anywhere in the
# policy book. Used to tell "fabricated number" apart from "real number
# belonging to the wrong policy".
_REAL_EXCESSES = {p["excess"] for p in POLICIES.values()}
_REAL_SUB_LIMITS = {v for p in POLICIES.values() for v in p["sub_limits"].values()}
_REAL_CLAIMED_AMOUNTS = {c["claimed_amount"] for c in CLAIMS.values()}


# =========================================================================
# Tool-choice accuracy
# =========================================================================

def sequence_matches(actual: list[str], valid_paths: list[list[str]]) -> bool:
    """A trajectory's sequence passes if it equals one of the valid paths."""
    return any(list(actual) == list(p) for p in valid_paths)


def best_path(actual: list[str], valid_paths: list[list[str]]) -> list[str]:
    """The valid path the agent came closest to — the one sharing the most
    tools in the same positions. Used for per-step reporting only."""
    def score(p: list[str]) -> tuple[int, int]:
        aligned = sum(1 for i, t in enumerate(p) if i < len(actual) and actual[i] == t)
        return (aligned, -abs(len(p) - len(actual)))
    return max(valid_paths, key=score)


def stepwise_tool_choice(actual: list[str], valid_paths: list[list[str]]) -> tuple[int, int]:
    """(matched_positions, compared_positions) against the closest valid
    path. Gives a partial-credit view alongside the strict case-level rate."""
    target = best_path(actual, valid_paths)
    span = max(len(target), len(actual))
    matched = sum(1 for i in range(span)
                  if i < len(target) and i < len(actual) and actual[i] == target[i])
    return matched, span


def tool_choice_accuracy(rows: list[dict]) -> float:
    """Fraction of cases whose full tool sequence matched a valid path."""
    if not rows:
        return 0.0
    return sum(1 for r in rows if r["sequence_match"]) / len(rows)


# =========================================================================
# Argument validity
# =========================================================================

def _issue(kind: str, message: str) -> dict:
    return {"kind": kind, "message": message}


def validate_call(tool: str, args: dict, case) -> list[dict]:
    """Check that a tool call's arguments point at entities that actually
    exist in the claim/policy data AND belong to the claim under evaluation.

    This deliberately goes past datatype checking: a string is not a valid
    claim_id merely because it is a string, and 2500 is not a valid excess
    merely because it is a number — it has to be *this* policy's excess.

    Returns a list of issues; empty means the call is valid.
    """
    issues: list[dict] = []
    if not isinstance(args, dict):
        return [_issue(INVALID_ARGUMENT, f"{tool}: arguments were not an object: {args!r}")]
    if "__raw__" in args:
        return [_issue(INVALID_ARGUMENT, f"{tool}: arguments were not parseable JSON: {args['__raw__']!r}")]

    claim = case.claim
    policy = case.policy

    if tool == "get_claim":
        cid = args.get("claim_id")
        if cid not in CLAIMS:
            issues.append(_issue(MADE_UP_INPUT, f"get_claim: claim_id {cid!r} does not exist in the claims system"))
        elif cid != case.claim_id:
            issues.append(_issue(INVALID_ARGUMENT, f"get_claim: claim_id {cid!r} is a real claim but not the one under evaluation ({case.claim_id})"))

    elif tool == "get_policy":
        pid = args.get("policy_id")
        if pid not in POLICIES:
            issues.append(_issue(MADE_UP_INPUT, f"get_policy: policy_id {pid!r} does not exist in the policy book"))
        elif pid != claim["policy_id"]:
            issues.append(_issue(INVALID_ARGUMENT, f"get_policy: policy_id {pid!r} is a real policy but not the one on claim {case.claim_id} ({claim['policy_id']})"))

    elif tool in ("check_exclusions", "check_policy_exclusions"):
        pid = args.get("policy_id")
        if pid not in POLICIES:
            issues.append(_issue(MADE_UP_INPUT, f"{tool}: policy_id {pid!r} does not exist in the policy book"))
        elif pid != claim["policy_id"]:
            issues.append(_issue(INVALID_ARGUMENT, f"{tool}: policy_id {pid!r} is a real policy but not the one on claim {case.claim_id} ({claim['policy_id']})"))

        cause = args.get("cause")
        if cause not in CAUSE_ENUM:
            issues.append(_issue(INVALID_ARGUMENT, f"{tool}: cause {cause!r} is outside the controlled vocabulary"))
        elif case.acceptable_causes and cause not in case.acceptable_causes:
            issues.append(_issue(INVALID_ARGUMENT, f"{tool}: cause {cause!r} is not a defensible reading of this claim's adjuster notes (expected one of {case.acceptable_causes})"))

    elif tool == "compute_payout":
        amount = args.get("claimed_amount")
        if not _num_eq(amount, claim["claimed_amount"]):
            if _num_in(amount, _REAL_CLAIMED_AMOUNTS):
                issues.append(_issue(INVALID_ARGUMENT, f"compute_payout: claimed_amount {amount!r} belongs to a different claim, not {case.claim_id} ({claim['claimed_amount']})"))
            else:
                issues.append(_issue(MADE_UP_INPUT, f"compute_payout: claimed_amount {amount!r} appears on no claim record (claim {case.claim_id} is {claim['claimed_amount']})"))

        excess = args.get("policy_excess")
        if excess is not None and not _num_eq(excess, policy["excess"]):
            if _num_in(excess, _REAL_EXCESSES):
                issues.append(_issue(INVALID_ARGUMENT, f"compute_payout: policy_excess {excess!r} is a real excess but not policy {policy['policy_id']}'s ({policy['excess']})"))
            else:
                issues.append(_issue(MADE_UP_INPUT, f"compute_payout: policy_excess {excess!r} matches no policy in the book"))

        status = args.get("claim_status")
        if status not in CLAIM_STATUS_ENUM:
            issues.append(_issue(INVALID_ARGUMENT, f"compute_payout: claim_status {status!r} is outside the controlled vocabulary"))

        sub_limit = args.get("sub_limit")
        if sub_limit is not None:
            policy_sub_limits = set(policy["sub_limits"].values())
            if not _num_in(sub_limit, policy_sub_limits):
                if _num_in(sub_limit, _REAL_SUB_LIMITS):
                    issues.append(_issue(INVALID_ARGUMENT, f"compute_payout: sub_limit {sub_limit!r} is a real sub-limit but not one on policy {policy['policy_id']}"))
                else:
                    issues.append(_issue(MADE_UP_INPUT, f"compute_payout: sub_limit {sub_limit!r} exists on no policy"))
    else:
        issues.append(_issue(WRONG_TOOL, f"{tool!r} is not a tool this agent has"))

    return issues


def _num_eq(a, b) -> bool:
    try:
        return a is not None and b is not None and abs(float(a) - float(b)) < 0.01
    except (TypeError, ValueError):
        return False


def _num_in(value, pool) -> bool:
    return any(_num_eq(value, v) for v in pool)


def argument_validity(rows: list[dict]) -> dict:
    """Aggregate argument validity across every recorded tool call."""
    total = sum(r["tool_calls_total"] for r in rows)
    valid = sum(r["tool_calls_valid"] for r in rows)
    return {
        "valid_calls": valid,
        "total_calls": total,
        "rate": (valid / total) if total else 0.0,
    }


# =========================================================================
# Step efficiency
# =========================================================================

def step_efficiency(steps_taken: int, min_required_steps: int) -> float:
    """steps_taken / minimum_required_steps. 1.0 is ideal, >1 is wasted
    work, <1 means the agent did less than the job requires."""
    if min_required_steps <= 0:
        return 0.0
    return steps_taken / min_required_steps


def aggregate_step_efficiency(rows: list[dict]) -> float:
    """Aggregate = total steps taken / total minimum steps, so a long case
    is not weighted the same as a one-step case."""
    total_taken = sum(r["steps_taken"] for r in rows)
    total_min = sum(r["min_required_steps"] for r in rows)
    return (total_taken / total_min) if total_min else 0.0


# =========================================================================
# Cost / latency distribution
# =========================================================================

def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile on a sorted copy. p50 of an even-length list
    is the lower of the two middle values (no interpolation), so the number
    reported is always one actually observed."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round(pct / 100 * len(ordered) + 0.5)) - 1))
    return ordered[idx]


def distribution(values: list[float]) -> dict:
    return {
        "p50": percentile(values, 50),
        "max": max(values) if values else 0.0,
        "min": min(values) if values else 0.0,
        "total": sum(values),
    }


# =========================================================================
# Failure classification
# =========================================================================

def _canonical(args) -> str:
    try:
        return json.dumps(args, sort_keys=True, default=str)
    except TypeError:
        return repr(args)


def classify_failures(case, trajectory: list[dict], termination_reason: str,
                      output: dict | None, arg_issues: list[dict]) -> list[str]:
    """Assign this case's trajectory zero or more taxonomy labels.

    A trajectory with no labels is a passing trajectory. The same function
    is used for the baseline and the post-mitigation run.
    """
    modes: set[str] = set()
    actual = [s["tool"] for s in trajectory]

    # --- silent give-up: stopped without a usable structured answer
    if output is None or termination_reason in GIVE_UP_REASONS:
        modes.add(SILENT_GIVE_UP)

    # --- argument problems, already diagnosed per call
    for issue in arg_issues:
        modes.add(issue["kind"])

    # --- a tool erroring out is an invalid argument by definition
    if any(s.get("error") for s in trajectory):
        modes.add(INVALID_ARGUMENT)

    # --- loop: the exact same call repeated
    seen: set[str] = set()
    for step in trajectory:
        key = step["tool"] + "|" + _canonical(step.get("arguments"))
        if key in seen:
            modes.add(LOOP)
        seen.add(key)

    matched = sequence_matches(actual, case.valid_paths)
    if not matched:
        missing = case.required_tools - set(actual)
        if missing:
            modes.add(SKIPPED_REQUIRED_STEP)

        extra_tools = set(actual) - case.allowed_tools
        if extra_tools:
            modes.add(WRONG_TOOL)

        if len(actual) > case.min_required_steps:
            modes.add(UNNECESSARY_STEPS)

        if not modes:
            # Sequence is wrong but every tool was allowed, none required is
            # missing, no repeats, no extra steps -> a pure mis-ordering.
            modes.add(OTHER)

    return sorted(modes)


def count_failure_modes(rows: list[dict]) -> dict:
    """Case counts per mode (a case is counted once per mode it exhibits)."""
    counts = {m: 0 for m in FAILURE_MODES}
    for r in rows:
        for m in r["failure_modes"]:
            counts[m] = counts.get(m, 0) + 1
    return counts


def top_failure_mode(counts: dict) -> tuple[str, int]:
    """Highest-count mode. Ties break on taxonomy order so the choice is
    deterministic across runs."""
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], FAILURE_MODES.index(kv[0])))
    return ranked[0]
