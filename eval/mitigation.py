"""Week 8 — Task Set D §7: the single mitigation, described for the report.

One mitigation, chosen after the baseline named its top failure mode. This
module holds only metadata — the change itself lives in
app/claims_agent/tools.py under "WEEK 8 §7 — THE ONE MITIGATION".
"""

from __future__ import annotations

from app.claims_agent.tools import (
    TASK_D_TOOL_SCHEMAS,
    TASK_D_MITIGATED_TOOL_SCHEMAS,
)

MITIGATION = {
    "name": "tighter tool description",
    "option": 1,
    "targets": "skipped_required_step",
    "code_changed": (
        "app/claims_agent/tools.py -> TASK_D_MITIGATED_TOOL_SCHEMAS: the "
        "`description` string of all four tools. Names, parameter schemas and "
        "implementations are untouched (asserted at import time)."
    ),
    "toggle": "run_agent(..., mitigation=True) selects MITIGATED_TOOL_SETS['task_d']",
    "rationale": (
        "The baseline's top failure mode was skipped_required_step: the agent "
        "answered correctly while never calling compute_payout, doing the "
        "subtraction itself. The baseline descriptions state only what each "
        "tool RETURNS — nothing marks a tool mandatory and nothing states a "
        "precondition — so from where the agent sits, skipping a step is not "
        "visibly wrong. A skipped step is a tool-selection failure, and tool "
        "selection is driven by the tool descriptions, so tightening them is "
        "the mitigation that acts on the actual cause. The four other allowed "
        "mitigations do not: a hard step limit constrains how MANY steps are "
        "taken, not which; argument validation fires only on a call that was "
        "already made, so it can never catch a call that never happened; "
        "re-planning adds a whole model round-trip; and replacing the agent "
        "with the deterministic workflow removes the agent rather than fixing "
        "it, and would make every trajectory metric trivially perfect."
    ),
    "not_done": (
        "no argument validation, no hard step limit, no re-planning step, no "
        "swap to the deterministic workflow, and no change to the system "
        "prompt, budgets, seed or agent loop"
    ),
}


def description_diff() -> list[dict]:
    """The before/after text of every description the mitigation changed."""
    rows = []
    for base, mit in zip(TASK_D_TOOL_SCHEMAS, TASK_D_MITIGATED_TOOL_SCHEMAS):
        rows.append({
            "tool": base["function"]["name"],
            "before": base["function"]["description"],
            "after": mit["function"]["description"],
        })
    return rows


def print_diff() -> None:
    print("=" * 72)
    print("MITIGATION DIFF — tool descriptions only")
    print("=" * 72)
    for row in description_diff():
        print(f"\n### {row['tool']}")
        print("- BEFORE: " + row["before"])
        print("+ AFTER : " + row["after"])


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print_diff()
