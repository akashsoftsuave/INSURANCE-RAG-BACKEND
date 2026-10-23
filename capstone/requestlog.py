"""Per-request log for the integrated system: ONE line per claim triaged, one trace id from FNOL in to referral out.

What is in the line (and why):
  trace_id            same id on the agent spans, the MCP-side spans (mcp_spans.jsonl) and the referral record
  spans               every span with hop (agent|mcp), stage, latency, tokens, cost  -> cost/latency per span
  wording_set / ids   which endorsement set was resolved for which loss date, the exact chunk ids cited and a
                      SHA-256 of each cited clause text -> a regulator can prove the same wording was applied
  limits, in_force    the inputs to the adjuster's decision, not the decision
  model, prompt_ver   what produced the model-written part

What is NOT in the line: the FNOL as received, adjuster notes, names, addresses, injuries. Only the redacted FNOL.
Internal reference ids (claim_id, policy_id) are structured fields on purpose: they are how a case is found again.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from threading import Lock

from capstone.config import LOG_DIR

_lock = Lock()
REQUEST_LOG = LOG_DIR / "requests.jsonl"


def corpus_hash() -> str:
    """Hash of the wording + limits + policy tables the answer was derived from (reproducibility)."""
    from capstone.config import DATA_DIR
    h = hashlib.sha256()
    for n in ("wording_clauses", "limits", "policies"):
        h.update((DATA_DIR / f"{n}.json").read_bytes())
    return h.hexdigest()[:16]


def build_record(out: dict) -> dict:
    return {
        "schema": "capstone-log-v1", "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "trace_id": out["trace_id"], "case_id": out.get("case_id"),
        "claim_id": out["claim_id"], "policy_id": out["policy_id"], "product": out["product"],
        "loss_date_used": out["loss_date_used"], "loss_date_on_file": out["loss_date_on_file"],
        "wording_set_expected": out["wording_set_expected_for_loss_date"], "wording_mode": out["wording_mode"],
        "tool_calls": [{"tool": t["tool"], "origin": t["origin"], "arguments": t["arguments"]} for t in out["trajectory"]],
        "corpus_sha256": corpus_hash(),
        "tool_sequence": out["tool_sequence"], "tools_discovered_over_mcp": out["tools_discovered_over_mcp"],
        "citations": [{"chunk_id": c["chunk_id"], "endorsement_id": c["endorsement_id"], "version": c["endorsement_version"],
                       "text_sha256": hashlib.sha256(c["excerpt"].encode()).hexdigest()[:16]} for c in out["citations"]],
        "wrong_version_citations": out["wrong_version_citations"],
        "in_force": out["in_force"], "limits": out["limits"], "referral": out["referral"],
        "guard": out["guard"], "stop_reason": out["stop_reason"],
        "fnol_redacted": out.get("fnol_redacted"), "summary_redacted": out["summary"],
        "model": out["model"], "prompt_version": out["prompt_version"],
        "spans": out["spans"], "totals": out["totals"],
        "redaction": {"claimant_pii_in_line": False, "method": "capstone.pii.scrub (structured fields + patterns)"},
    }


def write_request_log(out: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps(build_record(out), ensure_ascii=False)
    with _lock, REQUEST_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
