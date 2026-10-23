"""Local tools of the Week-12 agent (the MCP-served tools live in mcp_server.py and are discovered at runtime).

  lookup_policy            claim or policy number -> policy facts + REDACTED claim record
  check_coverage_in_force  policy period check against the LOSS date (+ which wording set applies)
  refer_to_adjuster        terminal action; writes the referral. The only way a triage ends.

None of these returns claimant PII: the claim record is scrubbed with its own structured PII
fields before it leaves this module, so the model never sees a name, address or injury.
"""
from __future__ import annotations

import json
import re
import time
from functools import lru_cache

from capstone import corpus, guard
from capstone.config import RUNS_DIR, version_for_date, ymd_int
from capstone.pii import scrub

CLAIM_RE = re.compile(r"CLM-\d{4}-[A-Z0-9]+", re.I)
POLICY_RE = re.compile(r"POL-(?:MOT|HOM)-\d{4}-\d{6}", re.I)


class ToolError(Exception):
    pass


@lru_cache(maxsize=1)
def _policies() -> dict:
    return {p["policy_id"]: p for p in corpus.load("policies")}


@lru_cache(maxsize=1)
def _claims() -> dict:
    return {c["claim_id"]: c for c in corpus.load("claims")}


def find_claim(claim_id: str) -> dict | None:
    return _claims().get((claim_id or "").upper())


def extract_ids(text: str) -> dict:
    c = CLAIM_RE.search(text or "")
    p = POLICY_RE.search(text or "")
    return {"claim_id": c.group(0).upper() if c else None, "policy_id": p.group(0).upper() if p else None}


# --------------------------------------------------------------------------
def lookup_policy(identifier: str) -> dict:
    ident = (identifier or "").strip().upper()
    claim = find_claim(ident)
    pid = claim["policy_id"] if claim else ident
    pol = _policies().get(pid)
    if pol is None:
        raise ToolError(f"No claim or policy found for {identifier!r}. Expected CLM-YYYY-xxxxx or POL-MOT-YYYY-nnnnnn / POL-HOM-YYYY-nnnnnn.")
    out = {
        "policy_id": pol["policy_id"], "product": pol["product"],
        "policy_limit": pol["sum_insured"],
        "flood_cover_addon_on_schedule": pol["flood_cover_addon"],
        "periods_of_insurance": pol["periods"],
    }
    if claim:
        out["claim_id"] = claim["claim_id"]
        out["loss_date_on_file"] = claim["loss_date"]
        out["adjuster_notes_redacted"] = scrub(claim["adjuster_notes"], claim["pii"])
        out["photo_captions"] = claim["photo_captions"]
    return out


LOOKUP_POLICY_SCHEMA = {"type": "function", "function": {
    "name": "lookup_policy",
    "description": "Look up a policy (product, policy limit, periods of insurance, flood add-on flag) and, for a claim number, the claim's loss date on file and redacted adjuster notes. Takes a claim number (CLM-YYYY-xxxxx) or a policy number (POL-MOT-/POL-HOM-...). Never decides coverage.",
    "parameters": {"type": "object", "properties": {"identifier": {"type": "string", "description": "Claim number or policy number."}}, "required": ["identifier"]}}}


# --------------------------------------------------------------------------
def check_coverage_in_force(policy_id: str, loss_date: str) -> dict:
    pol = _policies().get((policy_id or "").upper())
    if pol is None:
        raise ToolError(f"No policy found for {policy_id!r}.")
    try:
        ymd_int(loss_date)
    except Exception:
        raise ToolError(f"loss_date must be YYYY-MM-DD (e.g. 2024-08-11), got {loss_date!r}.")
    res = corpus.in_force(pol, loss_date)
    nearest = None
    if not res["in_force"]:
        nearest = [p for p in pol["periods"]]
    return {"policy_id": pol["policy_id"], "loss_date": loss_date, "in_force_on_loss_date": res["in_force"],
            "period_of_insurance": res["period"], "periods_on_file": nearest,
            "wording_set_in_force_on_loss_date": version_for_date(loss_date),
            "note": "Policy-period fact only. This is not a decision on the claim."}


CHECK_IN_FORCE_SCHEMA = {"type": "function", "function": {
    "name": "check_coverage_in_force",
    "description": "Check whether a policy had a period of insurance covering the LOSS date (not today), and which endorsement set (2023 or 2025) is in force on that date. Facts only, no coverage decision.",
    "parameters": {"type": "object", "properties": {
        "policy_id": {"type": "string"}, "loss_date": {"type": "string", "description": "Loss date, YYYY-MM-DD."}},
        "required": ["policy_id", "loss_date"]}}}


# --------------------------------------------------------------------------
def refer_to_adjuster(claim_id: str, reason: str, cited_clauses: list | None = None, trace_id: str | None = None) -> dict:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    reason, rguard = guard.enforce_summary(reason)   # the referral text is read by a human adjuster: same no-verdict rule
    rec = {"referral_id": f"REF-{int(time.time() * 1000) % 10**9:09d}", "claim_id": claim_id,
           "status": "REFERRED_TO_ADJUSTER", "reason": scrub(reason, (find_claim(claim_id) or {}).get("pii")),
           "cited_clauses": cited_clauses or [], "trace_id": trace_id}
    with (RUNS_DIR / "referrals.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return {"referral_id": rec["referral_id"], "status": rec["status"], "reason_guard_replaced": rguard["guard_replaced"]}


REFER_SCHEMA = {"type": "function", "function": {
    "name": "refer_to_adjuster",
    "description": "Hand the claim to a human adjuster with the facts and the cited clauses. This is how every triage ends; the adjuster, not the assistant, makes the coverage decision.",
    "parameters": {"type": "object", "properties": {
        "claim_id": {"type": "string"},
        "reason": {"type": "string", "description": "Neutral facts for the adjuster: loss date, period check result, wording version applied, clauses to read. No coverage verdict."},
        "cited_clauses": {"type": "array", "items": {"type": "string"}, "description": "Clause + endorsement ids, e.g. 'MOTOR 5.3 MOT-END-FLD-2023'."}},
        "required": ["claim_id", "reason"]}}}

LOCAL_TOOLS = {
    "lookup_policy": (lookup_policy, LOOKUP_POLICY_SCHEMA),
    "check_coverage_in_force": (check_coverage_in_force, CHECK_IN_FORCE_SCHEMA),
    "refer_to_adjuster": (refer_to_adjuster, REFER_SCHEMA),
}
