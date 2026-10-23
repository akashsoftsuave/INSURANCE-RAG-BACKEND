"""`python -m capstone eval` : the whole eval suite on the integrated system, one command.

  stage run    execute the agent for every case (resumable; stored in capstone/runs/<mode>/)
               + the 'naive' (no date filter) runs of the straddle cases = the BEFORE state
  stage score  assertions -> per-assertion / per-tag pass rates -> trajectory eval -> judge + agreement vs hand labels
               -> taxonomy counts -> retrieval-level wrong-version metric -> reports/*.md|json

Assertions come first; the judge only sees runs it is asked about, and its agreement with the hand labels is a number.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from capstone.config import REPORT_DIR
from capstone.evals import assertions, judge, trajectory
from capstone.evals.cases import build_cases
from capstone.evals.runner import load_run, run_cases

LABELS = REPORT_DIR.parent / "evals" / "labels_integrated.json"


def _pct(a, b):
    return round(100 * a / b, 1) if b else None


def score_mode(cases, mode):
    rows = []
    for c in cases:
        run = load_run(mode, c["case_id"])
        if run is None:
            continue
        if run.get("crashed"):
            rows.append({"case": c, "run": None, "asserts": {a: {"passed": False, "detail": "run crashed: " + run["error"]} for a in c["asserts"]}, "crash": run["error"]})
            continue
        rows.append({"case": c, "run": run, "asserts": assertions.run_assertions(c, run)})
    return rows


def summarise_suite(rows) -> dict:
    n = len(rows)
    per_assert = defaultdict(lambda: [0, 0])
    per_tag = defaultdict(lambda: [0, 0])
    case_pass = {}
    for r in rows:
        ok_all = True
        for name, res in r["asserts"].items():
            per_assert[name][1] += 1
            per_assert[name][0] += res["passed"]
            ok_all &= res["passed"]
        case_pass[r["case"]["case_id"]] = ok_all
        for t in r["case"]["tags"]:
            per_tag[t][1] += 1
            per_tag[t][0] += ok_all
    return {"n": n, "cases_all_assertions_pass": sum(case_pass.values()), "case_pass_pct": _pct(sum(case_pass.values()), n),
            "per_assertion": {k: {"passed": v[0], "n": v[1], "pct": _pct(*v)} for k, v in sorted(per_assert.items())},
            "per_tag": {k: {"passed": v[0], "n": v[1], "pct": _pct(*v)} for k, v in sorted(per_tag.items())},
            "per_case": case_pass}


def classify_modes(rows) -> dict:
    """Rule-based failure-mode assignment for the taxonomy (one run can carry several modes)."""
    modes = defaultdict(list)
    for r in rows:
        run, a, tid = r["run"], r["asserts"], (r["run"] or {}).get("trace_id")
        cid = r["case"]["case_id"]
        if run is None:
            modes["run_crashed"].append((cid, None))
            continue
        if not a.get("citation_version", {"passed": True})["passed"] and "WRONG-VERSION" in a["citation_version"]["detail"]:
            modes["wrong_endorsement_version_cited"].append((cid, tid))
        if run["guard"]["guard_replaced"] or run["referral"].get("reason_guard_replaced"):
            modes["verdict_language_from_model_caught_by_guard"].append((cid, tid))
        if not a.get("no_verdict_language", {"passed": True})["passed"]:
            modes["verdict_language_reached_adjuster"].append((cid, tid))
        if not a.get("tool_dates_consistent", {"passed": True})["passed"] or any(
                "loss_date" in str(s["result"].get("error", "")) for s in run["trajectory"]):
            modes["loss_date_drift_across_tool_boundary"].append((cid, tid))
        if not a.get("limits_three_numbers", {"passed": True})["passed"]:
            modes["limits_missing_or_wrong"].append((cid, tid))
        if run["referral"].get("forced_by_code"):
            modes["agent_ended_without_referral"].append((cid, tid))
        if not a.get("pii_clean", {"passed": True})["passed"]:
            modes["pii_in_output_or_logs"].append((cid, tid))
        if not a.get("trace_continuity", {"passed": True})["passed"]:
            modes["trace_id_lost_at_a_hop"].append((cid, tid))
        if not a.get("in_force_checked", {"passed": True})["passed"]:
            modes["in_force_check_wrong_or_missing"].append((cid, tid))
    return {k: {"count": len(v), "pct_of_runs": _pct(len(v), len(rows)), "examples": v[:3]} for k, v in modes.items()}


def agreement(items, labels, verdicts) -> dict:
    pairs = [(labels[cid]["label"], verdicts[cid]["verdict"]) for cid in labels if cid in verdicts]
    n = len(pairs)
    agree = sum(1 for h, j in pairs if h == j)
    tp = sum(1 for h, j in pairs if h == "FAIL" and j == "FAIL")
    fn = sum(1 for h, j in pairs if h == "FAIL" and j != "FAIL")
    fp = sum(1 for h, j in pairs if h == "PASS" and j == "FAIL")
    return {"n": n, "agreement_pct": _pct(agree, n), "human_fail_n": sum(1 for h, _ in pairs if h == "FAIL"),
            "judge_caught_human_fails": tp, "judge_missed_human_fails": fn, "judge_false_alarms": fp,
            "disagreements": [cid for cid in labels if cid in verdicts and labels[cid]["label"] != verdicts[cid]["verdict"]]}


def main(stage: str = "all", limit: int | None = None) -> None:
    cases = build_cases()[: limit]
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if stage in ("all", "run"):
        run_cases(cases, mode="in_force", label="in_force")
        run_cases([c for c in cases if "bonus5" in c["tags"]], mode="naive", label="naive(before)")
    if stage == "run":
        return
    out = {}
    rows = score_mode(cases, "in_force")
    out["suite"] = summarise_suite(rows)
    out["taxonomy_counts"] = classify_modes(rows)

    scores = [trajectory.score_run(r["case"], r["run"], r["asserts"]) for r in rows if r["run"]]
    out["trajectory"] = {"summary": trajectory.summarise(scores), "per_case": scores}

    before_rows = score_mode([c for c in cases if "bonus5" in c["tags"]], "naive")
    out["bonus5"] = {"before_naive": {r["case"]["case_id"]: r["asserts"].get("citation_version", {}).get("passed") for r in before_rows},
                     "after_in_force": {r["case"]["case_id"]: r["asserts"]["citation_version"]["passed"] for r in rows if "bonus5" in r["case"]["tags"]}}

    if LABELS.exists():
        labels = json.loads(LABELS.read_text(encoding="utf-8"))["labels"]
        items = [(r["case"], r["run"]) for r in rows if r["run"] and r["case"]["case_id"] in labels]
        out["judge_agreement"] = {}
        for ver in ("v2", "v3"):
            if judge.PROMPTS[ver].exists():
                verdicts = judge.judge_all(items, ver)
                out["judge_agreement"][ver] = {**agreement(items, labels, verdicts), "verdicts": {k: v["verdict"] for k, v in verdicts.items()}}
    (REPORT_DIR / "suite_results.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    s = out["suite"]
    print(f"\nSUITE  n={s['n']}  cases passing every assertion: {s['cases_all_assertions_pass']} ({s['case_pass_pct']}%)")
    for k, v in s["per_assertion"].items():
        print(f"  {k:26s} {v['passed']}/{v['n']}  {v['pct']}%")
    for k, v in s["per_tag"].items():
        print(f"  tag {k:14s} {v['passed']}/{v['n']}")
    t = out["trajectory"]["summary"]
    print(f"TRAJECTORY n={t['n']} outcome={t['outcome_pass_pct']}% trajectory={t['trajectory_pass_pct']}% gap={t['gap_points']}pts tool-choice={t['tool_choice_accuracy_full_sequence_pct']}%")
    for k, v in out.get("judge_agreement", {}).items():
        print(f"JUDGE {k}: agreement {v['agreement_pct']}% (n={v['n']})")
    print("TAXONOMY", {k: v["count"] for k, v in out["taxonomy_counts"].items()})
