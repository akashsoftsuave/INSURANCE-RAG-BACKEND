"""logq — slice the Week-11 request log from the command line.

The drill rule is "logs alone": no trace id, no claim number, no timestamp.
So every slice the complaint could map onto is a flag here:

    time         --since 24h / --until ...
    user         --user ADJ-0106
    prompt ver.  --prompt-version insurance-qa-v2
    input type   --input-type coverage_question
    cost outlier --cost-outlier            (cost > mean + 2*stdev, or top 5%)
    output       --verdict covered          (schema v2 only)
                 --contradictions           (schema v2 only: said covered AND
                                             exclusion text was in context)
    free text    --grep theft               (redacted question + answer)

    python -m week11_observability.logq stats
    python -m week11_observability.logq find --since 24h --input-type coverage_question
    python -m week11_observability.logq show <trace_id-prefix>
    python -m week11_observability.logq drill start --label drill-1
    python -m week11_observability.logq drill stop --found <trace_id> --slice input_type

A filter on a field the log line does not have is reported loudly, with the
schema of the lines that lack it — that message IS the "missing log field"
answer the drill write-up asks for.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import settings

DRILL_LOG = Path("week11_observability/logs/drill_runs.jsonl")


def load(path: str | None) -> list[dict]:
    p = Path(path or settings.REQUEST_LOG_PATH)
    if not p.exists():
        sys.exit(f"no request log at {p}")
    with p.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def parse_when(value: str) -> datetime:
    m = re.fullmatch(r"(\d+)([mhd])", value)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]
        return datetime.now(timezone.utc) - delta
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ts(rec: dict) -> datetime:
    return datetime.fromisoformat(rec["ts"])


def cost(rec: dict) -> float:
    return (rec.get("totals") or {}).get("cost_usd", 0.0)


def require_field(records: list[dict], field: str) -> list[dict]:
    have = [r for r in records if field in r]
    missing = Counter(r.get("schema") for r in records if field not in r)
    if missing:
        print(f"!! field '{field}' is ABSENT on {sum(missing.values())} line(s) "
              f"(schema: {dict(missing)}). Those lines cannot be sliced on it.", file=sys.stderr)
    return have


def apply_filters(records: list[dict], a) -> list[dict]:
    out = records
    if a.since:
        t = parse_when(a.since)
        out = [r for r in out if ts(r) >= t]
    if a.until:
        t = parse_when(a.until)
        out = [r for r in out if ts(r) <= t]
    if a.user:
        out = [r for r in out if r.get("user_id") == a.user]
    if a.prompt_version:
        out = [r for r in out if r.get("prompt_version") == a.prompt_version]
    if a.input_type:
        out = [r for r in out if r.get("input_type") == a.input_type]
    if a.collection:
        out = [r for r in out if r.get("collection") == a.collection]
    if a.verdict:
        out = [r for r in require_field(out, "coverage_verdict") if r["coverage_verdict"] == a.verdict]
    if a.contradictions:
        out = [r for r in require_field(out, "contradiction_flag") if r["contradiction_flag"]]
    if a.context_id:
        out = [r for r in out if any(c["chunk_id"] == a.context_id for c in r.get("context_ids", []))]
    if a.grep:
        rx = re.compile(a.grep, re.I)
        out = [r for r in out if rx.search(r.get("question_redacted") or "") or rx.search(r.get("answer_redacted") or "")]
    if a.cost_outlier:
        costs = [cost(r) for r in records]
        if len(costs) >= 2:
            mean, sd = statistics.mean(costs), statistics.pstdev(costs)
            p95 = sorted(costs)[max(0, int(len(costs) * 0.95) - 1)]
            cut = min(mean + 2 * sd, p95) if sd else p95
            out = [r for r in out if cost(r) >= cut]
            print(f"   cost-outlier cut: >= ${cut:.6f} (mean ${mean:.6f}, sd ${sd:.6f})", file=sys.stderr)
    return out


def one_line(r: dict, width: int) -> str:
    verdict = r.get("coverage_verdict", "-")
    flag = "!" if r.get("contradiction_flag") else " "
    ans = re.sub(r"\s+", " ", r.get("answer_redacted") or "")
    q = re.sub(r"\s+", " ", r.get("question_redacted") or "")
    return (f"{r['ts'][11:19]} {r['trace_id'][:8]} {r.get('user_id', '-'):9s} "
            f"{(r.get('prompt_version') or '-'):16s} {r.get('input_type', '-'):17s} "
            f"{verdict:11s}{flag} ${cost(r):.6f} | Q: {q[:width]} | A: {ans[:width]}")


def cmd_find(a) -> None:
    rows = apply_filters(load(a.log), a)
    print(f"{len(rows)} line(s)")
    for r in rows:
        print(one_line(r, a.width))


def cmd_stats(a) -> None:
    rows = apply_filters(load(a.log), a)
    print(f"{len(rows)} request(s), {rows[0]['ts'] if rows else '-'} .. {rows[-1]['ts'] if rows else '-'}")
    for field in ("schema", "user_id", "prompt_version", "input_type", "collection", "coverage_verdict"):
        c = Counter(r.get(field, "<absent>") for r in rows)
        print(f"  {field:16s} " + ", ".join(f"{k}={v}" for k, v in c.most_common()))
    if rows:
        costs = [cost(r) for r in rows]
        print(f"  cost/query      mean=${statistics.mean(costs):.6f} max=${max(costs):.6f}")


def cmd_show(a) -> None:
    rows = [r for r in load(a.log) if r["trace_id"].startswith(a.trace_id)]
    if not rows:
        sys.exit(f"no trace starting with {a.trace_id}")
    print(json.dumps(rows[0], indent=2, ensure_ascii=False))


def cmd_drill(a) -> None:
    DRILL_LOG.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    if a.action == "start":
        with DRILL_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "start", "label": a.label, "ts": now.isoformat()}) + "\n")
        print(f"drill '{a.label}' started at {now.isoformat(timespec='seconds')}")
        return
    events = [json.loads(line) for line in DRILL_LOG.read_text(encoding="utf-8").splitlines() if line.strip()]
    starts = [e for e in events if e["event"] == "start"]
    if not starts:
        sys.exit("no drill started")
    start = starts[-1]
    elapsed = (now - datetime.fromisoformat(start["ts"])).total_seconds()
    mmss = f"{int(elapsed // 60):02d}:{int(elapsed % 60):02d}"
    event = {"event": "stop", "label": start["label"], "ts": now.isoformat(),
             "found_trace_id": a.found, "slice": a.slice, "time_to_find": mmss,
             "elapsed_s": round(elapsed, 1)}
    with DRILL_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    print(f"drill '{start['label']}' stopped: time-to-find {mmss} (found {a.found}, slice {a.slice})")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="logq")
    ap.add_argument("--log", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_filters(p):
        p.add_argument("--since"); p.add_argument("--until")
        p.add_argument("--user"); p.add_argument("--prompt-version")
        p.add_argument("--input-type"); p.add_argument("--collection")
        p.add_argument("--verdict", choices=["covered", "not_covered", "partial", "not_stated", "n/a"])
        p.add_argument("--contradictions", action="store_true")
        p.add_argument("--context-id"); p.add_argument("--grep")
        p.add_argument("--cost-outlier", action="store_true")
        p.add_argument("--width", type=int, default=70)

    p = sub.add_parser("find"); add_filters(p); p.set_defaults(fn=cmd_find)
    p = sub.add_parser("stats"); add_filters(p); p.set_defaults(fn=cmd_stats)
    p = sub.add_parser("show"); p.add_argument("trace_id"); p.set_defaults(fn=cmd_show)
    p = sub.add_parser("drill"); p.add_argument("action", choices=["start", "stop"])
    p.add_argument("--label", default="drill"); p.add_argument("--found"); p.add_argument("--slice")
    p.set_defaults(fn=cmd_drill)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
