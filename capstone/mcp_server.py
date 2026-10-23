"""Our MCP server (Week 9 server, grown up): policy-wording search scoped by product AND loss date,
plus the limits table. Called live by a client that is not ours (see EXTERNAL_CLIENT.md).

Contract worth stating out loud:
  * date scoping is enforced here in code (capstone.retrieval, mode=in_force by default);
  * every tool takes an optional `trace_id` and echoes it back with a server-side span, so one id
    survives the agent -> MCP hop; each call is also appended to capstone/logs/mcp_spans.jsonl;
  * NOTHING returned contains claimant data: the server has no access path to claim records, only
    wording + limits tables; as defence in depth every response is run through redact_pii and a
    changed response is flagged `pii_redacted_on_response: true`;
  * the incoming `query` is redacted before it is logged.

Env: WORDING_MODE=in_force|naive  ("naive" exists ONLY to reproduce the before-state in evals).

Run:  python -m capstone.mcp_server        (stdio)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from app.core.redaction import redact_pii  # noqa: E402
from capstone import corpus  # noqa: E402
from capstone.config import LOG_DIR, PERILS, ymd_int  # noqa: E402
from capstone.retrieval import retrieve_wording  # noqa: E402

mcp = FastMCP("claims-policy-wording")
MODE = os.environ.get("WORDING_MODE", "in_force")
SPAN_LOG = LOG_DIR / "mcp_spans.jsonl"


def _log_span(rec: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with SPAN_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def _finish(tool: str, trace_id: str | None, t0: float, args: dict, payload: dict) -> dict:
    blob = json.dumps(payload)
    clean = redact_pii(blob)
    flagged = clean != blob
    if flagged:
        payload = json.loads(clean)
    latency = round((time.monotonic() - t0) * 1000, 1)
    span = {"trace_id": trace_id, "hop": "mcp", "tool": tool, "latency_ms": latency, "cost_usd": 0.0,
            "billing": "local CPU (embeddings + BM25 + cross-encoder); no provider charge",
            "mode": MODE, "pii_redacted_on_response": flagged}
    _log_span({**span, "args_redacted": {k: redact_pii(str(v)) for k, v in args.items()}})
    payload["_span"] = span
    payload["trace_id"] = trace_id
    return payload


@mcp.tool()
def search_policy_wording(query: str, product: str, loss_date: str, trace_id: str | None = None) -> dict:
    """Search the policy WORDING in force on the loss date.

    Returns clauses of the base wording and of the endorsement set whose effective dates contain
    `loss_date`, each with clause number, endorsement id + version, effective range, a citation
    string and a link. Wording from a version NOT in force on that date is never returned.

    Args:
        query: policy language to look for, e.g. "flood exclusion" or "theft keys left in vehicle".
        product: "motor" or "household".
        loss_date: date of LOSS, YYYY-MM-DD (not today's date, not the notification date).
        trace_id: request trace id to propagate (optional).
    """
    t0 = time.monotonic()
    if not (query or "").strip():
        raise ValueError("query is empty: pass policy language such as 'flood exclusion'.")
    if product not in ("motor", "household"):
        raise ValueError(f"product must be 'motor' or 'household', got {product!r}.")
    try:
        ymd_int(loss_date)
    except Exception:
        raise ValueError(f"loss_date must be YYYY-MM-DD (e.g. 2024-08-11), got {loss_date!r}. "
                         "Convert the date before calling; an unparseable date is rejected, never defaulted to today.")
    res = retrieve_wording(redact_pii(query), product, loss_date, mode=MODE, top_k=3)
    return _finish("search_policy_wording", trace_id, t0, {"query": query, "product": product, "loss_date": loss_date}, res)


@mcp.tool()
def get_limits(product: str, peril: str, loss_date: str, policy_id: str, trace_id: str | None = None) -> dict:
    """Get the three SEPARATE financial figures for a peril on a policy, as in force on the loss date:
    deductible (borne first by the insured), sub_limit (cap for this peril), policy_limit (the policy's sum insured).
    They are different numbers and must never be combined or substituted.

    Args:
        product: "motor" or "household".
        peril: one of flood, theft, collision, fire, wear_and_tear.
        loss_date: date of LOSS, YYYY-MM-DD.
        policy_id: the policy number, e.g. POL-MOT-2023-000001.
        trace_id: request trace id to propagate (optional).
    """
    t0 = time.monotonic()
    if peril not in PERILS:
        raise ValueError(f"peril must be one of {list(PERILS)}, got {peril!r}.")
    try:
        ymd_int(loss_date)
    except Exception:
        raise ValueError(f"loss_date must be YYYY-MM-DD, got {loss_date!r}.")
    pol = next((p for p in corpus.load("policies") if p["policy_id"] == (policy_id or "").upper()), None)
    if pol is None:
        raise ValueError(f"unknown policy_id {policy_id!r}.")
    row = corpus.limits_for(product, peril, loss_date)
    if row is None:
        raise ValueError(f"no limits row for {product}/{peril}.")
    res = {"deductible": row["deductible"], "sub_limit": row["sub_limit"], "policy_limit": pol["sum_insured"],
           "limits_version": row["version"], "peril": peril, "loss_date": loss_date, "currency": "INR",
           "reading_note": "deductible, sub_limit and policy_limit are three distinct figures; sub_limit null = no peril cap below the policy limit."}
    return _finish("get_limits", trace_id, t0, {"product": product, "peril": peril, "loss_date": loss_date, "policy_id": policy_id}, res)


if __name__ == "__main__":
    import contextlib

    from capstone.retrieval import _service
    # The Week 3-4 services print() progress to stdout; on a stdio MCP server stdout IS the protocol channel,
    # so any stray print corrupts a JSON-RPC frame. Route it to stderr while loading, and warm the models
    # before the handshake so spans measure search, not model load.
    with contextlib.redirect_stdout(sys.stderr):
        _service()
    mcp.run(transport="stdio")
