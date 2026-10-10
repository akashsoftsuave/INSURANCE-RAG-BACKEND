"""cost_by_stage.md and tenx.md, computed from the request log only.

    python -m week11_observability.cost_report --trace <trace_id-prefix> \
        --out-dir week11_observability/deliverables

Cost per query by stage is a SUM OVER SPANS (each span carries its stage),
so it can be computed before anything is optimised — attribution first.

The 10x question is answered against the limits the provider itself returned
on the logged responses (x-ratelimit-* headers captured on every generation
span), not against a number typed in from memory.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import datetime
from pathlib import Path

from app.claims_agent.pricing import get_rate
from week11_observability.logq import load

STAGES = ("retrieval", "generation", "tools")


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, round(p / 100 * (len(xs) - 1))))]


def peak_minute(records: list[dict]) -> tuple[int, int]:
    """Max requests and max generation tokens inside any rolling 60 s window."""
    pts = sorted((datetime.fromisoformat(r["ts"]).timestamp(), r["totals"]["tokens"]) for r in records)
    best_req = best_tok = 0
    j = 0
    for i in range(len(pts)):
        while pts[i][0] - pts[j][0] > 60:
            j += 1
        window = pts[j:i + 1]
        best_req = max(best_req, len(window))
        best_tok = max(best_tok, sum(t for _, t in window))
    return best_req, best_tok


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", required=True)
    ap.add_argument("--log", default=None)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--since", default=None, help="ISO timestamp: only lines at/after this define 'today'")
    ap.add_argument("--until", default=None)
    a = ap.parse_args()

    all_recs = [r for r in load(a.log) if r.get("prompt_version")]
    rec = next(r for r in all_recs if r["trace_id"].startswith(a.trace))
    day = all_recs
    if a.since:
        day = [r for r in day if r["ts"] >= a.since]
    if a.until:
        day = [r for r in day if r["ts"] <= a.until]
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    model = rec["model"]
    rate = get_rate(model)

    # ---------------- cost_by_stage.md ----------------
    L = ["# Cost per query, by stage", "",
         f"Request: `{rec['trace_id']}` · {rec['ts']} · user `{rec['user_id']}` · "
         f"prompt `{rec['prompt_version']}` · model `{model}`",
         f"Pricing: ${rate['input_per_m']}/1M input, ${rate['output_per_m']}/1M output "
         "(app/claims_agent/pricing.py).", "",
         "## This request (the drill's found trace)", "",
         "| Stage | Spans | Latency (ms) | Tokens (prompt / completion) | Cost (USD) | Share of cost |",
         "|---|---|---|---|---|---|"]
    total_cost = rec["totals"]["cost_usd"] or 1e-12
    for st in STAGES:
        spans = [s for s in rec["spans"] if s["stage"] == st]
        lat = sum(s["latency_ms"] for s in spans)
        pt = sum(s["tokens"]["prompt"] for s in spans)
        ct = sum(s["tokens"]["completion"] for s in spans)
        c = sum(s["cost_usd"] for s in spans)
        L.append(f"| {st} | {', '.join(s['name'] for s in spans)} | {lat:.1f} | {pt} / {ct} | "
                 f"${c:.8f} | {c / total_cost:.0%} |")
    L.append(f"| **total** | | **{rec['totals']['latency_ms']:.1f}** | **{rec['totals']['tokens']}** | "
             f"**${rec['totals']['cost_usd']:.8f}** | 100% |")
    gen = next(s for s in rec["spans"] if s["stage"] == "generation")
    L += ["",
          f"Generation arithmetic: {gen['tokens']['prompt']} × ${rate['input_per_m']}/1M + "
          f"{gen['tokens']['completion']} × ${rate['output_per_m']}/1M = ${gen['cost_usd']:.8f}.",
          "",
          "Retrieval (bge-small embed + Chroma vector + BM25 + ms-marco cross-encoder rerank) and tools "
          "(guardrail, evidence/source builder) run on local CPU in-process: **$0 provider cost per call**, "
          "which is why they are 0 here and not missing. Their cost is latency, shown above; retrieval "
          "sub-stages for this request: "
          + ", ".join(f"{k}={v}" for k, v in next(s for s in rec["spans"] if s["stage"] == "retrieval")
                      ["attrs"].get("sub_stage_ms", {}).items()) + ".",
          "",
          f"## All logged requests in the window ({len(day)} requests)", "",
          "| Stage | Mean cost/query | Mean latency (ms) | p95 latency (ms) |", "|---|---|---|---|"]
    for st in STAGES:
        costs = [r["totals"]["by_stage"][st]["cost_usd"] for r in day]
        lats = [r["totals"]["by_stage"][st]["latency_ms"] for r in day]
        L.append(f"| {st} | ${statistics.mean(costs):.8f} | {statistics.mean(lats):.1f} | {pct(lats, 95):.1f} |")
    tot_costs = [r["totals"]["cost_usd"] for r in day]
    L.append(f"| **total** | **${statistics.mean(tot_costs):.8f}** | "
             f"{statistics.mean(r['totals']['latency_ms'] for r in day):.1f} | "
             f"{pct([r['totals']['latency_ms'] for r in day], 95):.1f} |")
    (out / "cost_by_stage.md").write_text("\n".join(L) + "\n", encoding="utf-8")

    # ---------------- tenx.md ----------------
    rl = [r["rate_limit"] for r in day if r.get("rate_limit")]
    rpd_limit = rl[-1]["limit_requests_per_day"]
    tpm_limit = rl[-1]["limit_tokens_per_minute"]
    n_day = len(day)
    tok_q = statistics.mean(r["totals"]["tokens"] for r in day)
    peak_req, peak_tok = peak_minute(day)
    cost_q = statistics.mean(tot_costs)
    p95_lat = pct([r["totals"]["latency_ms"] for r in day], 95)

    ratios = {
        "rate limit — tokens/minute": (peak_tok * 10) / tpm_limit,
        "rate limit — requests/day": (n_day * 10) / rpd_limit,
    }
    first = max(ratios, key=ratios.get)
    T = ["# What breaks first at 10x", ""]
    if first.endswith("tokens/minute"):
        T.append(f"**Rate limit (tokens/minute) breaks first: today's peak minute burned {peak_tok:,} tokens "
                 f"({peak_req} requests × ~{tok_q:,.0f} tok), so 10x = {peak_tok * 10:,} TPM against Groq's "
                 f"{tpm_limit:,} TPM cap = {ratios[first]:.1f}x over — while cost only goes to "
                 f"${cost_q * n_day * 10:.4f}/day and latency is unchanged until the 429s start.**")
    else:
        T.append(f"**Rate limit (requests/day) breaks first: {n_day} requests today × 10 = {n_day * 10:,} "
                 f"against Groq's {rpd_limit:,} requests/day = {ratios[first]:.1f}x over.**")
    T += ["", "## The numbers behind the line (all from the request log)", "",
          "| Quantity | Today | At 10x | Limit | Headroom at 10x |", "|---|---|---|---|---|",
          f"| Requests/day | {n_day} | {n_day * 10:,} | {rpd_limit:,} RPD (header) | "
          f"{'OK' if n_day * 10 <= rpd_limit else 'BREAKS'} ({n_day * 10 / rpd_limit:.0%} of cap) |",
          f"| Peak-minute tokens | {peak_tok:,} ({peak_req} req) | {peak_tok * 10:,} | {tpm_limit:,} TPM (header) | "
          f"{'OK' if peak_tok * 10 <= tpm_limit else 'BREAKS'} ({peak_tok * 10 / tpm_limit:.0%} of cap) |",
          f"| Cost/query | ${cost_q:.6f} | ${cost_q:.6f} | — | per-query cost does not change with volume |",
          f"| Cost/day | ${cost_q * n_day:.4f} | ${cost_q * n_day * 10:.4f} | — | not a constraint |",
          f"| p95 latency | {p95_lat:,.0f} ms | {p95_lat:,.0f} ms + 429 back-off | — | "
          "latency only degrades as a *consequence* of the rate limit |",
          "",
          f"Max sustainable rate on this key: {tpm_limit:,} TPM / {tok_q:,.0f} tokens per query ≈ "
          f"{tpm_limit / tok_q:.1f} queries/minute, and {rpd_limit:,} queries/day in total.",
          "",
          f"Window: {day[0]['ts']} .. {day[-1]['ts']} ({n_day} requests). 'Today's volume' is the volume in "
          "this log; replace it with production volume and re-run — the limits come from the "
          "x-ratelimit-* headers Groq returned on these exact requests."]
    (out / "tenx.md").write_text("\n".join(T) + "\n", encoding="utf-8")
    print((out / "cost_by_stage.md").read_text(encoding="utf-8"))
    print((out / "tenx.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
