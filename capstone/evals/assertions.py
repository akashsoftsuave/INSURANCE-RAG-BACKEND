"""Deterministic assertions on an integrated run. These run BEFORE (and independent of) any LLM judge.

Every assertion is (case, run) -> (passed: bool, detail: str). They read the run output AND the on-disk
artifacts the system wrote (request log, MCP span log, referral file), so "trace id survives every hop" and
"PII not in logs" are checked against the files themselves, not against what the agent says it logged.
"""
from __future__ import annotations

import json
import re

from capstone import guard
from capstone.config import LOG_DIR, RUNS_DIR
from capstone.pii import leaked


def _read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _trace_lines(trace_id):
    req = [r for r in _read_jsonl(LOG_DIR / "requests.jsonl") if r.get("trace_id") == trace_id]
    mcp = [r for r in _read_jsonl(LOG_DIR / "mcp_spans.jsonl") if r.get("trace_id") == trace_id]
    ref = [r for r in _read_jsonl(RUNS_DIR / "referrals.jsonl") if r.get("trace_id") == trace_id]
    return req, mcp, ref


def referred(case, run):
    r = run["referral"]
    ok = r.get("status") == "REFERRED_TO_ADJUSTER" and bool(r.get("referral_id"))
    return ok, f"referral={r}"


def no_verdict_language(case, run):
    # What the adjuster RECEIVES: the summary and the stored referral reason. (Raw model text that the guard
    # rewrote is counted separately, as the 'caught by guard' mode in the taxonomy.)
    texts = [run["summary"]] + [r.get("reason", "") for r in _trace_lines(run["trace_id"])[2]]
    bad = sorted({v for t in texts for v in guard.violations(t)})
    return not bad, f"violations={bad}"


def loss_date_correct(case, run):
    exp = case["oracle"]["loss_date"]
    return run["loss_date_used"] == exp, f"used={run['loss_date_used']} expected={exp}"


def in_force_checked(case, run):
    o = case["oracle"]
    steps = [s for s in run["trajectory"] if s["tool"] == "check_coverage_in_force"]
    if not steps:
        return False, "check_coverage_in_force never called"
    ok_args = any(s["arguments"].get("loss_date") == o["loss_date"] for s in steps)
    good = [s for s in steps if "error" not in s["result"]]
    if not ok_args or not good:
        return False, f"loss_date args={[s['arguments'].get('loss_date') for s in steps]}"
    got = good[-1]["result"]["in_force_on_loss_date"]
    return got == o["in_force"], f"in_force got={got} expected={o['in_force']}"


def citation_version(case, run):
    o = case["oracle"]
    allowed = {"BASE", o["expected_set"]}
    wrong = [c["chunk_id"] for c in run["citations"] if c["endorsement_version"] not in allowed]
    if wrong:
        return False, f"WRONG-VERSION citations {wrong} (expected only BASE/{o['expected_set']})"
    if o.get("in_force") and o.get("peril") in ("flood", "theft") and o["expected_set"] in ("2023", "2025"):
        has = any(c["endorsement_version"] == o["expected_set"] and c["clause"] == o["governing_clause"] for c in run["citations"])
        if not has:
            return False, f"governing clause {o['governing_clause']} of the {o['expected_set']} set not cited"
    if o.get("in_force") and not run["citations"]:
        return False, "no wording cited"
    return True, f"cited={[c['chunk_id'] for c in run['citations']]}"


def citation_format(case, run):
    d = case["oracle"]["loss_date"]
    bad = []
    for c in run["citations"]:
        s = c["citation"]
        if d not in s or not c.get("url"):
            bad.append(c["chunk_id"])
        elif c["endorsement_version"] != "BASE" and not (c["endorsement_id"] in s and "endorsement set" in s):
            bad.append(c["chunk_id"])
    return not bad, f"bad_format={bad}"


def limits_three_numbers(case, run):
    exp, got = case["oracle"]["limits"], run["limits"]
    if not got:
        return False, "no limits returned (get_limits not called or failed)"
    diffs = {k: (got.get(k), exp[k]) for k in ("deductible", "sub_limit", "policy_limit") if got.get(k) != exp[k]}
    return not diffs, f"mismatch (got, expected)={diffs}"


def pii_clean(case, run):
    pii = case["oracle"].get("pii")
    req, mcp, ref = _trace_lines(run["trace_id"])
    blobs = {"run_output": json.dumps(run, ensure_ascii=False),
             "request_log": json.dumps(req, ensure_ascii=False),
             "mcp_span_log": json.dumps(mcp, ensure_ascii=False),
             "referral_file": json.dumps(ref, ensure_ascii=False)}
    # the run output legitimately holds the REDACTED fnol only; search every sink
    hits = {k: leaked(v, pii) for k, v in blobs.items()}
    hits = {k: v for k, v in hits.items() if v}
    return not hits, f"leaks={hits}"


def trace_continuity(case, run):
    tid = run["trace_id"]
    req, mcp, ref = _trace_lines(tid)
    mcp_steps = [s for s in run["trajectory"] if s["origin"] == "mcp"]
    problems = []
    if len(req) != 1:
        problems.append(f"request_log lines={len(req)}")
    if len(mcp) != len(mcp_steps):
        problems.append(f"mcp_span_log lines={len(mcp)} vs mcp calls={len(mcp_steps)}")
    if any(s["result"].get("trace_id") not in (tid, None) for s in mcp_steps if "error" not in s["result"]):
        problems.append("mcp response echoed a different trace id")
    if not ref:
        problems.append("referral record carries no matching trace id")
    if any(("latency_ms" not in sp or "cost_usd" not in sp) for sp in run["spans"]):
        problems.append("span without latency/cost")
    if not any(sp["attrs"].get("hop") == "mcp" for sp in run["spans"]) and mcp_steps:
        problems.append("no mcp-hop span in agent trace")
    return not problems, "; ".join(problems) or f"1 request line, {len(mcp)} mcp spans, {len(ref)} referral, id={tid}"


def tool_dates_consistent(case, run):
    exp = case["oracle"]["loss_date"]
    seen = [(s["tool"], s["arguments"].get("loss_date")) for s in run["trajectory"] if "loss_date" in s["arguments"]]
    bad = [(t, d) for t, d in seen if d != exp]
    return not bad, f"calls with wrong/unnormalised loss_date={bad}"


REGISTRY = {f.__name__: f for f in (referred, no_verdict_language, loss_date_correct, in_force_checked, citation_version,
                                    citation_format, limits_three_numbers, pii_clean, trace_continuity, tool_dates_consistent)}


def run_assertions(case, run) -> dict:
    out = {}
    for name in case["asserts"]:
        try:
            ok, detail = REGISTRY[name](case, run)
        except Exception as e:   # an assertion crashing is a failure, never a silent pass
            ok, detail = False, f"assertion error: {type(e).__name__}: {e}"
        out[name] = {"passed": bool(ok), "detail": detail}
    return out
