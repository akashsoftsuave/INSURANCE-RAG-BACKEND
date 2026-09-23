"""Server 2 — claims-system (THIRD PARTY).

Stands in for the "claims platform team's" MCP server from the Week 9
brief: claim status by claim number, and the adjuster note history behind
it. This file is deliberately NOT imported by, or wired into, our own
codebase (app/) — it has its own tiny in-memory dataset, exactly as an
external vendor's server would, so adding it to the host is provably a
config change and nothing else.

Run standalone for a smoke test:
    venv/Scripts/python.exe week9_mcp/servers/claims_system_server.py
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("claims-system")

# Independent "claims system" dataset — not app.claims_agent.data. A real
# third-party server's data is never something we can see or edit; this
# dict is just standing in for an API call we don't control.
_CLAIMS_DB: dict[str, dict] = {
    "CLM-2024-88120": {
        "claim_number": "CLM-2024-88120",
        "status": "under_review",
        "policy_id": "POL-AUTO-1001",
        "claimant_name": "R. Iyer",
        "opened_date": "2024-03-02",
        "last_updated": "2024-03-09",
    },
    "CLM-2024-77004": {
        "claim_number": "CLM-2024-77004",
        "status": "approved",
        "policy_id": "POL-HOME-2002",
        "claimant_name": "S. Menon",
        "opened_date": "2024-01-15",
        "last_updated": "2024-01-22",
    },
    "CLM-2024-61239": {
        "claim_number": "CLM-2024-61239",
        "status": "denied",
        "policy_id": "POL-AUTO-1004",
        "claimant_name": "A. Fernandes",
        "opened_date": "2023-11-30",
        "last_updated": "2023-12-06",
    },
}

_NOTES_DB: dict[str, list[dict]] = {
    "CLM-2024-88120": [
        {"date": "2024-03-02", "author": "adjuster:kpatel", "note": "FNOL received. Rear-end collision, claimant states other party ran red light. Photos attached."},
        {"date": "2024-03-05", "author": "adjuster:kpatel", "note": "Requested police report from claimant. Awaiting response."},
        {"date": "2024-03-09", "author": "adjuster:kpatel", "note": "Police report received, confirms claimant account. Moving to coverage review."},
    ],
    "CLM-2024-77004": [
        {"date": "2024-01-15", "author": "adjuster:rsingh", "note": "FNOL: pipe burst in upstairs bathroom, water damage to ceiling below. Plumber invoice attached."},
        {"date": "2024-01-19", "author": "adjuster:rsingh", "note": "Site inspection completed, damage consistent with sudden pipe failure, not wear and tear."},
        {"date": "2024-01-22", "author": "adjuster:rsingh", "note": "Approved for repair cost less excess."},
    ],
    "CLM-2024-61239": [
        {"date": "2023-11-30", "author": "adjuster:mwong", "note": "FNOL: vehicle damage, claimant initially reported collision with another vehicle."},
        {"date": "2023-12-03", "author": "adjuster:mwong", "note": "Bodyshop assessment inconsistent with reported collision; damage pattern matches curb strike."},
        {"date": "2023-12-06", "author": "adjuster:mwong", "note": "Claimant amended account to single-vehicle curb strike, a cause excluded under this policy's collision rider. Denied."},
    ],
}


@mcp.tool()
def get_claim_status(claim_number: str) -> dict:
    """Look up the current status of a claim by claim number.

    Args:
        claim_number: e.g. "CLM-2024-88120".

    Returns:
        status, policy_id, claimant_name, opened_date, last_updated.
    """
    claim = _CLAIMS_DB.get(claim_number)
    if claim is None:
        raise ValueError(f"claim {claim_number} not found: claim numbers look like CLM-YYYY-nnnnn")
    return dict(claim)


@mcp.tool()
def get_adjuster_notes(claim_number: str) -> dict:
    """Return the adjuster note history for a claim, oldest first.

    Args:
        claim_number: e.g. "CLM-2024-88120".

    Returns:
        claim_number and a chronological list of {date, author, note}.
    """
    notes = _NOTES_DB.get(claim_number)
    if notes is None:
        raise ValueError(f"claim {claim_number} not found: claim numbers look like CLM-YYYY-nnnnn")
    return {"claim_number": claim_number, "notes": notes}


if __name__ == "__main__":
    mcp.run(transport="stdio")
