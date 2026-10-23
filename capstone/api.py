"""HTTP face of the same entry point (optional; the CLI `python -m capstone triage` is the primary one).

  POST /api/v1/capstone/triage            {fnol, claim_id?}  -> the structured triage result (referral, never a verdict)
  GET  /api/v1/capstone/clause/{product}/{clause}?as_of=YYYY-MM-DD
                                          the clickable citation target: the clause text in force on that date
"""
from __future__ import annotations

import html

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from capstone.retrieval import clause_in_force

router = APIRouter(prefix="/api/v1/capstone", tags=["capstone"])
_agent = None


class TriageRequest(BaseModel):
    fnol: str
    claim_id: str | None = None


@router.post("/triage")
async def triage(req: TriageRequest):
    global _agent
    from capstone.agent import CapstoneAgent
    if _agent is None:
        _agent = CapstoneAgent()
        await _agent.connect()   # spawns our MCP server once and discovers its tools
    out = await _agent.triage(req.fnol, claim_id=req.claim_id)
    return {k: v for k, v in out.items() if k != "trajectory"}


@router.get("/clause/{product}/{clause}", response_class=HTMLResponse)
def clause(product: str, clause: str, as_of: str):
    try:
        rows = clause_in_force(product, clause, as_of)
    except Exception:
        raise HTTPException(400, "as_of must be YYYY-MM-DD")
    if not rows:
        raise HTTPException(404, f"no clause {clause} for {product} in force on {as_of}")
    body = "".join(
        f"<h3>{html.escape(r['product'].upper())} cl.{html.escape(r['clause'])} - {html.escape(r['title'])}</h3>"
        f"<p><b>{html.escape(r['endorsement_id'] or 'Base wording')}</b> | version {html.escape(r['endorsement_version'])} | "
        f"effective {r['effective_from']} to {r['effective_to']} | resolved for {html.escape(as_of)}</p><blockquote>{html.escape(r['text'])}</blockquote>"
        for r in rows)
    return f"<html><body style='font-family:sans-serif;max-width:720px;margin:2em auto'>{body}</body></html>"
