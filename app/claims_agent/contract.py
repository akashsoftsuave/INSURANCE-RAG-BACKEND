from __future__ import annotations

CONTRACT_FIELDS = [
    "claim_id", "status", "covered", "exclusion",
    "gross_amount", "excess", "payable_amount", "reason",
]

# Fields graded for pass/fail. "reason" is free text (model-generated
# narrative) and is intentionally excluded from grading, same rule for both
# systems.
GRADED_FIELDS = ["status", "covered", "exclusion", "gross_amount", "excess", "payable_amount"]


def make_output(claim_id: str, status: str, covered: bool, exclusion: str | None,
                 gross_amount, excess, payable_amount, reason: str) -> dict:
    return {
        "claim_id": claim_id,
        "status": status,
        "covered": covered,
        "exclusion": exclusion,
        "gross_amount": gross_amount,
        "excess": excess,
        "payable_amount": payable_amount,
        "reason": reason,
    }


def _num_eq(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        return abs(float(a) - float(b)) < 0.01
    except (TypeError, ValueError):
        return False


def grade(output: dict | None, expected: dict) -> tuple[bool, list[str]]:
    """Returns (passed, mismatches). A missing/None output fails outright."""
    if not isinstance(output, dict):
        return False, ["no structured output produced"]

    mismatches = []
    if output.get("status") != expected["status"]:
        mismatches.append(f"status: got {output.get('status')!r} want {expected['status']!r}")
    if bool(output.get("covered")) != bool(expected["covered"]):
        mismatches.append(f"covered: got {output.get('covered')!r} want {expected['covered']!r}")
    if output.get("exclusion") != expected["exclusion"]:
        mismatches.append(f"exclusion: got {output.get('exclusion')!r} want {expected['exclusion']!r}")
    if not _num_eq(output.get("gross_amount"), expected["gross_amount"]):
        mismatches.append(f"gross_amount: got {output.get('gross_amount')!r} want {expected['gross_amount']!r}")
    if not _num_eq(output.get("excess"), expected["excess"]):
        mismatches.append(f"excess: got {output.get('excess')!r} want {expected['excess']!r}")
    if not _num_eq(output.get("payable_amount"), expected["payable_amount"]):
        mismatches.append(f"payable_amount: got {output.get('payable_amount')!r} want {expected['payable_amount']!r}")

    return (len(mismatches) == 0), mismatches
