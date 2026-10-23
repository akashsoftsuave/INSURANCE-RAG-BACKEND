"""Retrieval-level before -> after on the named metric: WRONG-ENDORSEMENT-VERSION CITATION RATE.

No LLM involved, so n can be the whole claim book. For every claim file (n=500) the retriever is asked, in both
modes, the question an adjuster would ask, with the claim's product and loss date. A request counts as WRONG when
any of its top-3 citations belongs to an endorsement set that was not in force on the loss date.

Reported separately (the brief: "the average hides the regression"):
  straddle     flood/theft losses dated inside the 2023 set (2025 wording is the wrong, better-ranking text)
  post_2025    flood/theft losses dated on/after 2025-03-01
  base_only    collision / fire / wear-and-tear (no endorsement governs): the "don't straddle" population for hit-rate@3
and, for the cost of the date filter, hit-rate@3 = the governing clause of the CORRECT version is in the top 3.
"""
from __future__ import annotations

import json

from capstone import corpus
from capstone.config import REPORT_DIR, version_for_date
from capstone.retrieval import retrieve_wording

QUERIES = {
    "flood": "flood or water damage exclusion and conditions",
    "theft": "theft exclusion keys left unattended unlocked forced entry",
    "collision": "accidental collision damage insured peril",
    "fire": "fire damage insured peril",
    "wear_and_tear": "wear and tear exclusion",
}
GOVERNING = {("motor", "flood"): "5.3", ("household", "flood"): "6.2", ("motor", "theft"): "5.4", ("household", "theft"): "6.4",
             ("motor", "collision"): "4.1", ("household", "collision"): "4.1", ("motor", "fire"): "4.2", ("household", "fire"): "4.1",
             ("motor", "wear_and_tear"): "5.1", ("household", "wear_and_tear"): "6.1"}


def group(c: dict) -> str | None:
    v = version_for_date(c["loss_date"])
    if v == "PRE_2023":
        return None
    if c["peril"] in ("flood", "theft"):
        return "straddle" if v == "2023" else "post_2025"
    return "base_only"


def measure(claims: list[dict], mode: str, query_style: str) -> dict:
    rows = []
    for c in claims:
        g = group(c)
        if g is None:
            continue
        q = QUERIES[c["peril"]] if query_style == "keyword" else c["fnol_text"].split(" This is ")[0].strip()
        res = retrieve_wording(q, c["product"], c["loss_date"], mode=mode, top_k=3)
        exp = version_for_date(c["loss_date"])
        cits = res["citations"]
        wrong = [x["chunk_id"] for x in cits if x["endorsement_version"] not in ("BASE", exp)]
        gov = GOVERNING[(c["product"], c["peril"])]
        want_version = exp if c["peril"] in ("flood", "theft") else "BASE"
        hit = any(x["clause"] == gov and x["endorsement_version"] == want_version for x in cits)
        rank1_wrong = bool(cits) and cits[0]["endorsement_version"] not in ("BASE", exp)
        rows.append({"claim_id": c["claim_id"], "group": g, "loss_date": c["loss_date"], "wrong": bool(wrong),
                     "rank1_wrong": rank1_wrong, "hit3": hit, "violations": res["date_filter_violations"]})
    out = {}
    for g in ("straddle", "post_2025", "base_only", "ALL"):
        rs = [r for r in rows if g == "ALL" or r["group"] == g]
        n = len(rs)
        out[g] = {"n": n, "wrong_version_rate_pct": round(100 * sum(r["wrong"] for r in rs) / n, 1) if n else None,
                  "rank1_wrong_pct": round(100 * sum(r["rank1_wrong"] for r in rs) / n, 1) if n else None,
                  "hit_rate_at_3_pct": round(100 * sum(r["hit3"] for r in rs) / n, 1) if n else None,
                  "date_filter_violations": sum(r["violations"] for r in rs)}
    return {"summary": out, "rows": rows}


def main() -> dict:
    claims = corpus.load("claims")
    result = {}
    for style in ("keyword", "fnol"):
        for mode in ("naive", "in_force"):
            print(f"measuring {style}/{mode} on {len(claims)} claims", flush=True)
            result[f"{style}/{mode}"] = measure(claims, mode, style)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "version_metric.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    for k, v in result.items():
        print(k, json.dumps(v["summary"]))
    return result


if __name__ == "__main__":
    main()
