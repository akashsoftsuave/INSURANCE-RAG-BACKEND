"""Shared constants for the Week-12 capstone (Claims Triage & Coverage Assistant).

Everything date-related lives here so the retriever, the tools, the corpus
builder and the evals cannot disagree about when a wording version applies.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "capstone" / "data"
RUNS_DIR = ROOT / "capstone" / "runs"
LOG_DIR = ROOT / "capstone" / "logs"
REPORT_DIR = ROOT / "capstone" / "reports"

COLLECTION = "capstone_policy_wording"
CHROMA_PATH = str(ROOT / "capstone" / "chroma")

# Wording versions. BASE applies to every date; the endorsement sets replace
# specific clauses (flood, theft) for a bounded date range.
OPEN_START = 19000101
OPEN_END = 99991231
VERSIONS = {
    "BASE": (OPEN_START, OPEN_END),
    "2023": (20230101, 20250228),
    "2025": (20250301, OPEN_END),
}
ENDORSEMENT_2025_EFFECTIVE = "2025-03-01"

PRODUCTS = ("motor", "household")
PERILS = ("flood", "theft", "collision", "fire", "wear_and_tear")

# The three tools the agent may use locally; MCP tools are discovered at runtime.
LOCAL_TOOLS = ("lookup_policy", "check_coverage_in_force", "refer_to_adjuster")

# Words the system must never use to state a coverage outcome in its own voice.
# Enforced in capstone/guard.py (code), asserted in the eval suite.
# V1 shipped first and let "the endorsement covers damage to a parked vehicle" through (trace
# cc5cc24bbafe4405, capstone/evidence/). V2 closes it. V1 is kept ONLY so the failure->test loop can be re-run.
FORBIDDEN_V1 = (
    r"\bis\s+covered\b", r"\bare\s+covered\b", r"\bwill\s+be\s+covered\b",
    r"\bnot\s+covered\b", r"\bfully\s+covered\b", r"\bcovered\b",
    r"\bclaim\s+(?:is\s+|will\s+be\s+)?(?:approved|accepted|payable|declined|denied|rejected)\b",
    r"\bwe\s+will\s+pay\b", r"\bwill\s+be\s+paid\b", r"\bentitled\s+to\b",
)
FORBIDDEN_VERDICT_PATTERNS = FORBIDDEN_V1 + (
    r"\bcovers?\b", r"\bcover\s+(?:applies|is|does|will|would)\b", r"\buncovered\b",
    r"\b(?:is|are|be)\s+(?:not\s+)?(?:eligible|payable|admissible|recoverable)\b",
    r"\b(?:approve[sd]?|declines?|declined|denie[sd]|rejects?|rejected|accepts?)\b(?=[^.]{0,40}\bclaim\b)",
    r"\b(?:insurer|company|we|insurance)\s+(?:will|would|shall|must)\s+(?:pay|reimburse|indemnify|refuse)\b",
    r"\bshould\s+be\s+(?:paid|accepted|declined)\b",
)


def ymd_int(date_iso: str) -> int:
    """'2024-08-11' -> 20240811. Raises ValueError on anything else."""
    y, m, d = date_iso.split("-")
    if len(y) != 4 or len(m) != 2 or len(d) != 2:
        raise ValueError(f"loss date must be YYYY-MM-DD, got {date_iso!r}")
    return int(y) * 10000 + int(m) * 100 + int(d)


def version_for_date(date_iso: str) -> str:
    """Which endorsement set is in force on this date. Ground truth for evals."""
    n = ymd_int(date_iso)
    for v in ("2023", "2025"):
        lo, hi = VERSIONS[v]
        if lo <= n <= hi:
            return v
    return "PRE_2023"  # before any endorsement set existed
