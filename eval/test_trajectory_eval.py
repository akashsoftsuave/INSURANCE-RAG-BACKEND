"""Tests for the Week 8 (Task Set D) trajectory evaluation.

Plain asserts, no pytest dependency — the same convention as
eval/test_deterministic_checks.py, since pytest is not installed in this
project's venv.

Every fixture below is built from the REAL claim/policy data in
app/claims_agent/data.py and the REAL cases in eval/trajectory_cases.py.
No invented claim numbers, policy ids or amounts, except where a test is
specifically about detecting a fabricated one — those are marked.

Usage: python -m eval.test_trajectory_eval
"""

from __future__ import annotations

from app.claims_agent.data import CLAIMS, POLICIES
from eval.trajectory_cases import CASES, CASES_BY_ID, FULL_PATHS
from eval.trajectory_eval import evaluate_case, summarize
from eval.trajectory_metrics import (
    FAILURE_MODES,
    INVALID_ARGUMENT,
    LOOP,
    MADE_UP_INPUT,
    SILENT_GIVE_UP,
    SKIPPED_REQUIRED_STEP,
    UNNECESSARY_STEPS,
    WRONG_TOOL,
    aggregate_step_efficiency,
    argument_validity,
    classify_failures,
    count_failure_modes,
    percentile,
    sequence_matches,
    step_efficiency,
    tool_choice_accuracy,
    top_failure_mode,
    validate_call,
)

# =========================================================================
# helpers — build a trajectory/result out of real data
# =========================================================================

def _args_for(tool: str, case, **overrides) -> dict:
    """Correct arguments for `tool` on `case`, taken from the real records."""
    claim = CLAIMS[case.claim_id]
    policy = POLICIES[claim["policy_id"]]
    base = {
        "get_claim": {"claim_id": case.claim_id},
        "get_policy": {"policy_id": claim["policy_id"]},
        "check_exclusions": {"policy_id": claim["policy_id"],
                             "cause": case.acceptable_causes[0]},
        "compute_payout": {"claimed_amount": claim["claimed_amount"],
                           "policy_excess": policy["excess"],
                           "claim_status": case.expected_outcome["status"],
                           "sub_limit": None},
    }[tool]
    return {**base, **overrides}


def _trajectory(case, sequence: list[str], arg_overrides: dict | None = None) -> list[dict]:
    arg_overrides = arg_overrides or {}
    steps = []
    for i, tool in enumerate(sequence, 1):
        steps.append({
            "order": i,
            "iteration": i,
            "tool": tool,
            "arguments": _args_for(tool, case, **arg_overrides.get(tool, {}))
                         if tool in ("get_claim", "get_policy", "check_exclusions", "compute_payout")
                         else {},
            "result": {},
            "error": None,
            "latency_ms": 1,
            "iteration_tokens": 100,
            "iteration_cost": 0.0001,
        })
    return steps


_DEFAULT = object()  # so that output=None can mean "the agent produced nothing"


def _result(case, sequence, *, output=_DEFAULT, termination="completed",
            arg_overrides=None, cost=0.001, latency=1000, tokens=5000) -> dict:
    """A synthetic run_agent() return value for `case`."""
    if output is _DEFAULT:
        output = dict(case.expected_outcome, claim_id=case.claim_id, reason="x")
    return {
        "claim_id": case.claim_id,
        "output": output,
        "termination_reason": termination,
        "trajectory": _trajectory(case, sequence, arg_overrides),
        "tool_sequence": list(sequence),
        "steps_taken": len(sequence),
        "seed": 7,
        "iterations": len(sequence) + 1,
        "tokens_total": tokens,
        "cost_total": cost,
        "wall_clock_ms": latency,
        "log": [],
    }


CLEAN = CASES_BY_ID["TC-01"]        # full-pipeline case, 2 valid orderings
PENDING = CASES_BY_ID["TC-03"]      # empty notes, 1-step and 2-step paths
SUBLIMIT = CASES_BY_ID["TC-06"]     # PARTIAL, sub-limit binds


# =========================================================================
# 1. expected trajectory matching
# =========================================================================

def test_expected_trajectory_matches():
    seq = ["get_claim", "get_policy", "check_exclusions", "compute_payout"]
    assert sequence_matches(seq, CLEAN.valid_paths)
    row = evaluate_case(CLEAN, _result(CLEAN, seq))
    assert row["sequence_match"] is True, row
    assert row["trajectory_pass"] is True, row
    assert row["failure_modes"] == [], row


def test_expected_trajectory_for_the_one_step_case():
    row = evaluate_case(PENDING, _result(PENDING, ["get_claim"]))
    assert row["trajectory_pass"] is True, row
    assert row["step_efficiency"] == 1.0, row


# =========================================================================
# 2. alternate valid trajectory matching
# =========================================================================

def test_alternate_ordering_is_accepted():
    """get_policy and check_exclusions both need only the policy_id, so the
    reverse ordering is equally legitimate and must NOT be penalised."""
    alternate = ["get_claim", "check_exclusions", "get_policy", "compute_payout"]
    assert alternate in FULL_PATHS
    assert sequence_matches(alternate, CLEAN.valid_paths)
    row = evaluate_case(CLEAN, _result(CLEAN, alternate))
    assert row["trajectory_pass"] is True, row
    assert row["failure_modes"] == [], row
    assert row["step_efficiency"] == 1.0, row


def test_alternate_path_on_the_pending_review_case():
    """TC-03 accepts stopping at the claim record OR confirming the unknown
    cause against the policy. Both are valid; the second is longer."""
    row = evaluate_case(PENDING, _result(PENDING, ["get_claim", "check_exclusions"]))
    assert row["trajectory_pass"] is True, row
    assert row["failure_modes"] == [], row
    # Longer than the shortest valid path, but still valid -> efficiency > 1
    # without any failure label attached.
    assert row["step_efficiency"] == 2.0, row


def test_every_full_pipeline_case_declares_both_orderings():
    multi = [c for c in CASES if len(c.valid_paths) > 1]
    assert len(multi) == 10, "all 10 cases should offer more than one legitimate path"


# =========================================================================
# 3. invalid trajectory detection
# =========================================================================

def test_skipped_required_step_is_detected():
    """The right-answer-wrong-path shape: everything but compute_payout."""
    seq = ["get_claim", "get_policy", "check_exclusions"]
    assert not sequence_matches(seq, CLEAN.valid_paths)
    row = evaluate_case(CLEAN, _result(CLEAN, seq))
    assert row["trajectory_pass"] is False, row
    assert SKIPPED_REQUIRED_STEP in row["failure_modes"], row


def test_payout_without_checking_exclusions_is_detected():
    """The exact scenario Task Set D asks for: compute_payout reached
    without the coverage decision, on a clean claim, so the OUTCOME is
    still correct while the TRAJECTORY is not."""
    seq = ["get_claim", "get_policy", "compute_payout"]
    row = evaluate_case(CLEAN, _result(CLEAN, seq))
    assert row["outcome_pass"] is True, row
    assert row["trajectory_pass"] is False, row
    assert SKIPPED_REQUIRED_STEP in row["failure_modes"], row
    assert row["right_answer_wrong_path"] is True, row


def test_payout_correct_but_excess_missing_is_flagged():
    """The real observed shape: compute_payout is skipped, so `excess` comes
    back null while the decision and the money are right. The strict grader
    fails it; the payout-level flag still catches it as right-answer-wrong-
    path, which is what Task Set D is asking for."""
    seq = ["get_claim", "get_policy", "check_exclusions"]
    partial = dict(CLEAN.expected_outcome, claim_id=CLEAN.claim_id,
                   reason="x", excess=None)
    row = evaluate_case(CLEAN, _result(CLEAN, seq, output=partial))
    assert row["outcome_pass"] is False, row["outcome_mismatches"]
    assert row["outcome_mismatches"] == ["excess: got None want 3000"], row
    assert row["payout_correct"] is True, row
    assert row["right_answer_wrong_path"] is False, row
    assert row["payout_correct_wrong_path"] is True, row
    assert SKIPPED_REQUIRED_STEP in row["failure_modes"], row


def test_payout_correct_requires_the_payable_amount_to_match():
    seq = ["get_claim", "get_policy", "check_exclusions"]
    wrong_money = dict(CLEAN.expected_outcome, claim_id=CLEAN.claim_id,
                       reason="x", excess=None, payable_amount=999)
    row = evaluate_case(CLEAN, _result(CLEAN, seq, output=wrong_money))
    assert row["payout_correct"] is False, row
    assert row["payout_correct_wrong_path"] is False, row


def test_rescore_reproduces_a_saved_run_exactly():
    """Saved traces must re-derive identical metrics with no model calls."""
    from eval.trajectory_eval import rescore
    rows = [evaluate_case(c, _result(c, c.valid_paths[0])) for c in CASES]
    payload = {"phase": "before", "mitigation": False, "seed": 7,
               "rows": rows, "summary": summarize(rows)}
    again = rescore(payload)
    assert again["summary"] == payload["summary"], "rescoring changed the numbers"
    assert [r["failure_modes"] for r in again["rows"]] == \
           [r["failure_modes"] for r in payload["rows"]]


def test_wrong_tool_is_detected():
    """compute_payout is in no valid path for the PENDING_REVIEW case."""
    row = evaluate_case(PENDING, _result(PENDING, ["get_claim", "compute_payout"]))
    assert WRONG_TOOL in row["failure_modes"], row
    assert row["trajectory_pass"] is False, row


def test_unnecessary_steps_are_detected():
    seq = ["get_claim", "get_policy", "check_exclusions", "compute_payout", "get_policy"]
    row = evaluate_case(CLEAN, _result(CLEAN, seq))
    assert UNNECESSARY_STEPS in row["failure_modes"], row
    assert row["step_efficiency"] > 1.0, row


def test_loop_is_detected():
    seq = ["get_claim", "get_claim", "get_policy", "check_exclusions", "compute_payout"]
    row = evaluate_case(CLEAN, _result(CLEAN, seq))
    assert LOOP in row["failure_modes"], row


def test_silent_give_up_is_detected():
    row = evaluate_case(CLEAN, _result(CLEAN, ["get_claim"], output=None,
                                       termination="wall_clock"))
    assert SILENT_GIVE_UP in row["failure_modes"], row
    assert row["outcome_pass"] is False, row


def test_hallucinated_tool_counts_as_wrong_tool_and_give_up():
    """A provider-rejected call to a tool that does not exist."""
    result = _result(CLEAN, ["get_claim"], output=None, termination="hallucinated_tool")
    result["trajectory"].append({
        "order": 2, "iteration": 2, "tool": "json", "arguments": {},
        "result": {"error": "attempted to call tool 'json' which was not in the request"},
        "error": "attempted to call tool 'json' which was not in the request",
        "latency_ms": 0, "iteration_tokens": 0, "iteration_cost": 0.0,
        "hallucinated": True,
    })
    row = evaluate_case(CLEAN, result)
    assert WRONG_TOOL in row["failure_modes"], row
    assert SILENT_GIVE_UP in row["failure_modes"], row


# =========================================================================
# 4. argument validity detection
# =========================================================================

def test_real_arguments_are_valid():
    assert validate_call("get_claim", {"claim_id": "CLM-2026-00007"}, CLEAN) == []
    assert validate_call("get_policy", {"policy_id": "POL-AUTO-1003"}, CLEAN) == []


def test_fabricated_claim_id_is_made_up_input():
    # CLM-2026-99999 is deliberately absent from CLAIMS.
    issues = validate_call("get_claim", {"claim_id": "CLM-2026-99999"}, CLEAN)
    assert [i["kind"] for i in issues] == [MADE_UP_INPUT], issues


def test_fabricated_policy_id_is_made_up_input():
    # POL-AUTO-9999 is deliberately absent from POLICIES.
    issues = validate_call("get_policy", {"policy_id": "POL-AUTO-9999"}, CLEAN)
    assert [i["kind"] for i in issues] == [MADE_UP_INPUT], issues


def test_real_but_wrong_policy_is_invalid_argument_not_made_up():
    """POL-HOME-2001 exists, but it is not TC-01's policy. The point of the
    check is that datatype and existence are both fine and it is still wrong."""
    issues = validate_call("get_policy", {"policy_id": "POL-HOME-2001"}, CLEAN)
    assert [i["kind"] for i in issues] == [INVALID_ARGUMENT], issues


def test_another_claims_amount_is_invalid_argument():
    """350000 is a real claimed_amount (CLM-2026-00004) but not TC-01's."""
    issues = validate_call("compute_payout", _args_for("compute_payout", CLEAN,
                                                       claimed_amount=350000), CLEAN)
    kinds = [i["kind"] for i in issues]
    assert INVALID_ARGUMENT in kinds, issues


def test_invented_excess_is_made_up_input():
    # 4321 is not the excess of any policy in the book.
    issues = validate_call("compute_payout", _args_for("compute_payout", CLEAN,
                                                       policy_excess=4321), CLEAN)
    assert [i["kind"] for i in issues] == [MADE_UP_INPUT], issues


def test_real_excess_from_the_wrong_policy_is_invalid_argument():
    # 2500 is POL-HOME-2003's excess; TC-01's policy POL-AUTO-1003 has 3000.
    issues = validate_call("compute_payout", _args_for("compute_payout", CLEAN,
                                                       policy_excess=2500), CLEAN)
    assert [i["kind"] for i in issues] == [INVALID_ARGUMENT], issues


def test_sub_limit_from_the_wrong_policy_is_invalid_argument():
    # 50000 is POL-HOME-2002's theft sub-limit; TC-01's policy has none.
    issues = validate_call("compute_payout", _args_for("compute_payout", CLEAN,
                                                       sub_limit=50000), CLEAN)
    assert [i["kind"] for i in issues] == [INVALID_ARGUMENT], issues


def test_correct_sub_limit_is_valid():
    assert validate_call("compute_payout",
                         _args_for("compute_payout", SUBLIMIT, sub_limit=50000),
                         SUBLIMIT) == []


def test_cause_outside_the_vocabulary_is_invalid():
    issues = validate_call("check_exclusions",
                           {"policy_id": "POL-AUTO-1003", "cause": "meteor"}, CLEAN)
    assert [i["kind"] for i in issues] == [INVALID_ARGUMENT], issues


def test_indefensible_cause_reading_is_invalid():
    """'flood' is a legal enum value but not a defensible reading of TC-01's
    collision notes — datatype-only validation would let this through."""
    issues = validate_call("check_exclusions",
                           {"policy_id": "POL-AUTO-1003", "cause": "flood"}, CLEAN)
    assert [i["kind"] for i in issues] == [INVALID_ARGUMENT], issues


def test_both_defensible_cause_readings_are_accepted():
    for cause in CLEAN.acceptable_causes:
        assert validate_call("check_exclusions",
                             {"policy_id": "POL-AUTO-1003", "cause": cause}, CLEAN) == []


def test_argument_validity_rate_aggregates():
    good = evaluate_case(CLEAN, _result(CLEAN, FULL_PATHS[0]))
    bad = evaluate_case(CLEAN, _result(CLEAN, FULL_PATHS[0],
                                       arg_overrides={"get_policy": {"policy_id": "POL-AUTO-9999"}}))
    agg = argument_validity([good, bad])
    assert agg["total_calls"] == 8, agg
    assert agg["valid_calls"] == 7, agg
    assert abs(agg["rate"] - 7 / 8) < 1e-9, agg


# =========================================================================
# 5. outcome-vs-trajectory gap calculation
# =========================================================================

def test_gap_is_reported_in_percentage_points():
    """9 correct answers, 7 correct paths -> a 20 percentage-point gap."""
    rows = []
    # 7 cases: right answer AND right path
    for case in CASES[:7]:
        rows.append(evaluate_case(case, _result(case, case.valid_paths[0])))
    # 2 cases: right answer, WRONG path (compute_payout skipped)
    for case in CASES[7:9]:
        rows.append(evaluate_case(case, _result(case, case.valid_paths[0][:-1])))
    # 1 case: wrong answer and wrong path
    last = CASES[9]
    rows.append(evaluate_case(last, _result(last, last.valid_paths[0][:-1], output=None,
                                            termination="wall_clock")))

    s = summarize(rows)
    assert s["outcome_pass"] == 9, s
    assert s["trajectory_pass"] == 7, s
    assert abs(s["outcome_pass_rate"] - 0.9) < 1e-9, s
    assert abs(s["trajectory_pass_rate"] - 0.7) < 1e-9, s
    assert abs(s["outcome_vs_trajectory_gap_pp"] - 20.0) < 1e-9, s
    assert s["right_answer_wrong_path_cases"] == ["TC-08", "TC-09"], s


def test_gap_is_zero_when_every_correct_answer_took_a_valid_path():
    rows = [evaluate_case(c, _result(c, c.valid_paths[0])) for c in CASES]
    s = summarize(rows)
    assert s["outcome_vs_trajectory_gap_pp"] == 0.0, s
    assert s["right_answer_wrong_path_cases"] == [], s


# =========================================================================
# 6. failure-mode classification
# =========================================================================

def test_taxonomy_has_the_eight_required_modes():
    assert FAILURE_MODES == [
        "wrong_tool", "invalid_argument", "unnecessary_steps", "loop",
        "made_up_input", "silent_give_up", "skipped_required_step", "other",
    ]


def test_classification_is_empty_for_a_clean_run():
    traj = _trajectory(CLEAN, FULL_PATHS[0])
    assert classify_failures(CLEAN, traj, "completed", {"status": "APPROVED"}, []) == []


def test_classification_can_return_several_modes_at_once():
    seq = ["get_claim", "get_claim", "get_policy"]
    traj = _trajectory(CLEAN, seq)
    modes = classify_failures(CLEAN, traj, "wall_clock", None, [])
    assert LOOP in modes and SKIPPED_REQUIRED_STEP in modes and SILENT_GIVE_UP in modes, modes


def test_counts_cover_every_mode_even_at_zero():
    rows = [evaluate_case(c, _result(c, c.valid_paths[0])) for c in CASES]
    counts = count_failure_modes(rows)
    assert set(counts) == set(FAILURE_MODES), counts
    assert all(v == 0 for v in counts.values()), counts


def test_top_failure_mode_picks_the_highest_count():
    counts = {m: 0 for m in FAILURE_MODES}
    counts["skipped_required_step"] = 3
    counts["wrong_tool"] = 1
    assert top_failure_mode(counts) == ("skipped_required_step", 3)


def test_top_failure_mode_breaks_ties_deterministically():
    counts = {m: 0 for m in FAILURE_MODES}
    counts["loop"] = 2
    counts["wrong_tool"] = 2
    # wrong_tool comes first in FAILURE_MODES, so it wins the tie every time.
    assert top_failure_mode(counts) == ("wrong_tool", 2)


def test_verdict_and_taxonomy_never_disagree():
    """evaluate_case asserts this internally; exercise it across shapes."""
    shapes = [
        (FULL_PATHS[0], "completed"),
        (FULL_PATHS[1], "completed"),
        (["get_claim", "get_policy", "check_exclusions"], "completed"),
        (["get_claim", "get_policy", "compute_payout"], "completed"),
        (["get_claim"], "wall_clock"),
    ]
    for seq, term in shapes:
        out = None if term != "completed" else dict(CLEAN.expected_outcome, reason="x")
        row = evaluate_case(CLEAN, _result(CLEAN, seq, output=out, termination=term))
        assert row["trajectory_pass"] == (not row["failure_modes"]), (seq, row["failure_modes"])


# =========================================================================
# 7. before/after metric calculation
# =========================================================================

def test_step_efficiency_per_case_and_aggregate():
    assert step_efficiency(4, 4) == 1.0
    assert step_efficiency(3, 4) == 0.75
    assert step_efficiency(8, 4) == 2.0
    rows = [{"steps_taken": 3, "min_required_steps": 4},
            {"steps_taken": 4, "min_required_steps": 4}]
    assert abs(aggregate_step_efficiency(rows) - 7 / 8) < 1e-9


def test_percentile_reports_an_observed_value_not_a_mean():
    values = [1.0, 2.0, 3.0, 100.0]
    assert percentile(values, 50) == 2.0
    assert max(values) == 100.0
    # the mean (26.5) is not reported anywhere, by design
    assert percentile(values, 50) != sum(values) / len(values)


def test_before_after_delta_for_the_top_mode():
    # Only full-pipeline cases can "skip the payout step"; TC-03's shortest
    # valid path is a single call, so it is not a candidate.
    full = [c for c in CASES if len(c.valid_paths[0]) > 1]
    skipped_before = {c.case_id for c in full[:3]}
    skipped_after = {c.case_id for c in full[:1]}

    before_rows, after_rows = [], []
    for case in CASES:
        seq_b = case.valid_paths[0][:-1] if case.case_id in skipped_before else case.valid_paths[0]
        before_rows.append(evaluate_case(case, _result(case, seq_b, cost=0.0010, latency=1000)))
        seq_a = case.valid_paths[0][:-1] if case.case_id in skipped_after else case.valid_paths[0]
        after_rows.append(evaluate_case(case, _result(case, seq_a, cost=0.0013, latency=1200)))

    b, a = summarize(before_rows), summarize(after_rows)
    assert b["failure_mode_counts"]["skipped_required_step"] == 3, b["failure_mode_counts"]
    assert a["failure_mode_counts"]["skipped_required_step"] == 1, a["failure_mode_counts"]
    delta = a["failure_mode_counts"]["skipped_required_step"] - b["failure_mode_counts"]["skipped_required_step"]
    assert delta == -2

    # and the price of that improvement is measured, not waved away
    assert a["cost_usd"]["p50"] > b["cost_usd"]["p50"]
    assert a["latency_ms"]["p50"] - b["latency_ms"]["p50"] == 200.0
    assert a["trajectory_pass_rate"] > b["trajectory_pass_rate"]


def test_no_mode_is_silently_dropped_from_the_regression_table():
    rows = [evaluate_case(c, _result(c, c.valid_paths[0])) for c in CASES]
    before, after = count_failure_modes(rows), count_failure_modes(rows)
    table = [{"mode": m, "before": before[m], "after": after[m],
              "delta": after[m] - before[m]} for m in FAILURE_MODES]
    assert len(table) == 8
    assert {r["mode"] for r in table} == set(FAILURE_MODES)


def test_tool_choice_accuracy_is_a_case_level_rate():
    rows = [
        {"sequence_match": True}, {"sequence_match": True},
        {"sequence_match": True}, {"sequence_match": False},
    ]
    assert tool_choice_accuracy(rows) == 0.75


# =========================================================================
# runner
# =========================================================================

def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = []
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed.append((t.__name__, e))
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001 - a crashing test is a failing test
            failed.append((t.__name__, e))
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
