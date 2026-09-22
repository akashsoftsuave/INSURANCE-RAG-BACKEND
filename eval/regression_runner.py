import json
from pathlib import Path

from eval.deterministic_checks import run_all_checks, FAIL as CHECK_FAIL

TRACES_PATH = Path("traces/traces.jsonl")
LABELS_PATH = Path("eval/labels_27.json")
RESULTS_PATH = Path("eval/regression_results.json")


def load_traces():
    with TRACES_PATH.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_labels():
    return json.loads(LABELS_PATH.read_text(encoding="utf-8"))["labels"]


def score_trace(trace: dict, reference: dict) -> dict:
    text = trace.get("final_answer_redacted") or trace.get("raw_output_redacted") or ""
    checks = run_all_checks(text)
    det_failures = [c for c in checks if c["status"] == CHECK_FAIL]

    verdict = "FAIL" if det_failures else reference["label"]

    return {
        "trace_id": trace["trace_id"],
        "question_index": reference["question_index"],
        "dataset": reference["dataset"],
        "mode": reference["mode"],
        "reference_label": reference["label"],
        "deterministic_checks": checks,
        "deterministic_override": bool(det_failures),
        "verdict": verdict,
    }


def main():
    traces = load_traces()
    labels = load_labels()

    if len(traces) < 25:
        print(f"[WARN] traces/traces.jsonl has only {len(traces)} records — "
              f"active Task Set D population must be at least 25.")

    rows = []
    for trace in traces:
        reference = labels.get(trace["trace_id"])
        if reference is None:
            print(f"[WARN] trace {trace['trace_id']} has no entry in {LABELS_PATH} — skipping")
            continue
        rows.append(score_trace(trace, reference))

    n = len(rows)
    passed = sum(1 for r in rows if r["verdict"] == "PASS")

    by_mode = {}
    for r in rows:
        m = by_mode.setdefault(r["mode"], {"total": 0, "pass": 0})
        m["total"] += 1
        if r["verdict"] == "PASS":
            m["pass"] += 1

    by_dataset = {}
    for r in rows:
        d = by_dataset.setdefault(r["dataset"], {"total": 0, "pass": 0})
        d["total"] += 1
        if r["verdict"] == "PASS":
            d["pass"] += 1

    check_summary = {}
    for r in rows:
        for c in r["deterministic_checks"]:
            s = check_summary.setdefault(c["check"], {"PASS": 0, "FAIL": 0, "NOT_APPLICABLE": 0})
            s[c["status"]] += 1

    disagreements = [r for r in rows if r["deterministic_override"]]

    results = {
        "population": n,
        "pass": passed,
        "fail": n - passed,
        "pass_rate": round(passed / n, 4) if n else None,
        "by_mode": {
            mode: {**v, "pass_rate": round(v["pass"] / v["total"], 4)}
            for mode, v in sorted(by_mode.items())
        },
        "by_dataset": {
            ds: {**v, "pass_rate": round(v["pass"] / v["total"], 4)}
            for ds, v in sorted(by_dataset.items())
        },
        "deterministic_check_summary": check_summary,
        "deterministic_overrides": [r["trace_id"] for r in disagreements],
        "rows": rows,
    }

    RESULTS_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Regression run — {n} traces (traces/traces.jsonl)")
    print(f"Overall: {passed}/{n} PASS ({results['pass_rate']:.0%})\n")

    print("Pass rate by mode:")
    for mode, v in sorted(results["by_mode"].items()):
        print(f"  {mode:6s} {v['pass']}/{v['total']}  ({v['pass_rate']:.0%})")

    print("\nPass rate by dataset:")
    for ds, v in sorted(results["by_dataset"].items()):
        print(f"  {ds:18s} {v['pass']}/{v['total']}  ({v['pass_rate']:.0%})")

    print(f"\nTask Set D deterministic checks (across all {n} active traces):")
    for check, counts in check_summary.items():
        print(f"  {check:28s} PASS={counts['PASS']:2d}  FAIL={counts['FAIL']:2d}  N/A={counts['NOT_APPLICABLE']:2d}")

    if disagreements:
        print(f"\n[!] {len(disagreements)} trace(s) where a deterministic check overrode the reference label to FAIL:")
        for r in disagreements:
            print(f"    {r['trace_id']} (Q{r['question_index']}, {r['dataset']})")
    else:
        print(f"\nNo deterministic-check overrides — automated checks agree with the reference baseline on all {n} active traces.")

    print(f"\nWrote {RESULTS_PATH}")


if __name__ == "__main__":
    main()
