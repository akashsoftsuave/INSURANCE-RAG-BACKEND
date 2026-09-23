"""BEFORE snapshot of policy_search_server.py's search_policy_documents tool
— kept only so error_before_after.md's transcript can be regenerated
against a real MCP server. Not referenced by any config; not part of the
running system. See policy_search_server.py for the shipped (AFTER) tool
and week9_mcp/README.md #5 for why this pair exists.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402
from app.services.retrieval_service import RetrievalService  # noqa: E402

mcp = FastMCP("policy-search")

_retriever: RetrievalService | None = None


def _get_retriever() -> RetrievalService:
    global _retriever
    if _retriever is None:
        _retriever = RetrievalService()
    return _retriever


@mcp.tool()
def search_policy_documents(query: str, top_k: int = 3) -> dict:
    """Searches policy documents."""
    if not query or not query.strip():
        raise ValueError("Error: invalid input")
    if query.strip().upper().startswith("CLM-"):
        raise ValueError("Error: invalid input")
    if not (1 <= top_k <= 10):
        raise ValueError("Error: invalid input")

    retriever = _get_retriever()
    results = retriever.retrieve(query.strip())[:top_k]
    return {
        "query": query,
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
