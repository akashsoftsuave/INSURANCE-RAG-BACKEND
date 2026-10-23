"""Regulator replay: "why was claim 4471-A handled this way, eighteen months from now?"

Given only a trace id, re-derive the EVIDENCE the adjuster was shown and prove it is byte-identical:
  * same wording version  -> re-run each logged search_policy_wording call (same query/product/loss date, in_force)
                             and compare the returned chunk ids and the SHA-256 of every cited clause text
  * same figures          -> recompute deductible / sub-limit / policy limit from the tables
  * same policy-period    -> recompute the in-force check
  * same corpus           -> compare the logged corpus hash to the current tables
  * same model/prompt     -> reported from the log (the model-written summary is NOT regenerated: an LLM call is
                             not a proof. The stored, logged summary is what the adjuster actually received.)

What this does and does not prove is stated in the result under `limits_of_this_proof`.
"""
from __future__ import annotations

import hashlib
import json

from capstone import corpus
from capstone.config import LOG_DIR
from capstone.requestlog import corpus_hash
from capstone.retrieval import retrieve_wording


def replay(trace_id: str) -> dict:
    lines = [json.loads(l) for l in (LOG_DIR / "requests.jsonl").read_text(encoding="utf-8").splitlines()
             if l.strip() and trace_id in l]
    rec = next((r for r in lines if r["trace_id"] == trace_id), None)
    if rec is None:
        return {"trace_id": trace_id, "found": False}
    checks = {}
    logged = {c["chunk_id"]: c["text_sha256"] for c in rec["citations"]}
    got, mismatched = {}, []
    for call in rec["tool_calls"]:
        if call["tool"] != "search_policy_wording" or "error" in call.get("arguments", {}):
            continue
        a = call["arguments"]
        try:
            res = retrieve_wording(a["query"], a["product"], a["loss_date"], mode=rec["wording_mode"], top_k=3)
        except Exception as e:
            mismatched.append(f"search re-run failed: {e}")
            continue
        for c in res["citations"]:
            got[c["chunk_id"]] = hashlib.sha256(c["text"].encode()).hexdigest()[:16]
    for cid, sha in logged.items():
        if got.get(cid) != sha:
            mismatched.append(f"{cid}: logged {sha} vs replay {got.get(cid)}")
    checks["same_chunks_and_text"] = {"ok": not mismatched, "logged": len(logged), "replayed": len(got), "mismatches": mismatched}

    lim_call = next((c for c in rec["tool_calls"] if c["tool"] == "get_limits"), None)
    if lim_call:
        a = lim_call["arguments"]
        row = corpus.limits_for(a["product"], a["peril"], a["loss_date"])
        pol = next(p for p in corpus.load("policies") if p["policy_id"] == rec["policy_id"])
        now = {"deductible": row["deductible"], "sub_limit": row["sub_limit"], "policy_limit": pol["sum_insured"]}
        checks["same_figures"] = {"ok": now == rec["limits"], "logged": rec["limits"], "recomputed": now}
    pol = next((p for p in corpus.load("policies") if p["policy_id"] == rec["policy_id"]), None)
    if pol and rec["loss_date_used"]:
        now = corpus.in_force(pol, rec["loss_date_used"])["in_force"]
        checks["same_in_force"] = {"ok": rec["in_force"] is not None and now == rec["in_force"]["in_force_on_loss_date"], "recomputed": now}
    checks["same_corpus"] = {"ok": corpus_hash() == rec["corpus_sha256"], "logged": rec["corpus_sha256"], "now": corpus_hash()}
    return {"trace_id": trace_id, "found": True, "claim_id": rec["claim_id"], "loss_date": rec["loss_date_used"],
            "wording_set_applied": sorted({c["version"] for c in rec["citations"]}), "model": rec["model"],
            "prompt_version": rec["prompt_version"], "summary_as_delivered": rec["summary_redacted"],
            "referral": rec["referral"], "checks": checks, "all_evidence_reproduced": all(c["ok"] for c in checks.values()),
            "limits_of_this_proof": ["The model-written summary is stored, not regenerated; a re-run at temperature 0 is best-effort, not a proof.",
                                     "Hash covers the synthetic tables in capstone/data; a real deployment needs the wording PDFs under version control.",
                                     "Provider-side model weights can change behind a model name; the log records the name only."]}
