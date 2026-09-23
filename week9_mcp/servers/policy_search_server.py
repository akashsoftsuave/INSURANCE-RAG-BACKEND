"""Server 1 — policy-document search (OURS).

This is "our own" MCP server from the Week 9 task: it wraps the existing
RAG retrieval stack (app.services.retrieval_service.RetrievalService) that
was already built for the chat endpoint, and exposes it as a single MCP
tool. Nothing in app/services is modified — this file only imports and
calls it.

Run standalone for a smoke test:
    venv/Scripts/python.exe week9_mcp/servers/policy_search_server.py
It talks MCP over stdio, so a bare run just sits waiting for JSON-RPC on
stdin; it is meant to be launched by an MCP client (see host/agent.py or
scripts/capture_wire.py), not used interactively.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# The MCP client launches this file as `python .../policy_search_server.py`,
# which puts this file's own directory on sys.path[0], not the repo root and
# not the caller's cwd. Insert the repo root explicitly so `app.*` imports
# resolve no matter where the client process's cwd is.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402
from app.services.retrieval_service import RetrievalService  # noqa: E402

mcp = FastMCP("policy-search")

# Loaded once per server process (embedding model + cross-encoder), reused
# across every tools/call — this is the same RetrievalService the chat
# endpoint already uses, so results are identical to the running app.
_retriever: RetrievalService | None = None


def _get_retriever() -> RetrievalService:
    global _retriever
    if _retriever is None:
        _retriever = RetrievalService()
    return _retriever


CLAIM_ID_RE = re.compile(r"^CLM-\d{4}-\d+$", re.IGNORECASE)


@mcp.tool()
def search_policy_documents(query: str, top_k: int = 3) -> dict:
    """Search the insurance POLICY DOCUMENTS (coverage terms, exclusions,
    sub-limits, definitions) for passages relevant to a natural-language
    question about policy wording.

    Call this when you need to know what a policy SAYS — not what a claim
    IS. This tool has no knowledge of any specific claim, claimant, or
    claim status. For that, use the claims-system server's
    get_claim_status / get_adjuster_notes tools instead.

    Args:
        query: A question or phrase about policy terms, e.g. "flood
            exclusion clause" or "sub-limit for theft claims". Must be
            policy language, not a claim number or claimant name.
        top_k: How many ranked passages to return. 1-10, default 3.

    Returns:
        top_k matching passages, each with its source document/page/
        section metadata and a relevance score, ranked best-first.
    """
    q = (query or "").strip()
    if not q:
        raise ValueError(
            "search query is empty: pass specific policy language to "
            "search for, e.g. 'flood exclusion clause' or 'sub-limit for "
            "theft claims' — not a claim ID or numeric amount."
        )
    if CLAIM_ID_RE.match(q):
        raise ValueError(
            f"'{q}' looks like a claim number, not a policy-search query: "
            "this tool searches POLICY WORDING (coverage, exclusions, "
            "limits) and has no record of individual claims. Call the "
            "claims-system server's get_claim_status tool with this claim "
            "number instead."
        )
    if not (1 <= top_k <= 10):
        raise ValueError(
            f"top_k must be between 1 and 10, got {top_k}. Try top_k=3 for "
            "a focused answer or top_k=10 for broad coverage."
        )

    retriever = _get_retriever()
    results = retriever.retrieve(q)[:top_k]
    return {
        "query": q,
        "result_count": len(results),
        "results": [
            {
                "chunk_id": r.get("id"),
                "page": r.get("metadata", {}).get("page"),
                "section": r.get("metadata", {}).get("section"),
                "text": r.get("document", ""),
                "relevance_score": r.get("rerank_score", r.get("score")),
            }
            for r in results
        ],
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
