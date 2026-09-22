from __future__ import annotations

from app.claims_agent.data import CLAIMS, POLICIES

CAUSE_ENUM = ["flood", "theft", "accidental_damage", "fire", "wear_and_tear", "collision", "general", "unknown"]
CLAIM_STATUS_ENUM = ["APPROVED", "DENIED", "PARTIAL", "PENDING_REVIEW"]


class ToolError(Exception):
    pass


# ---------------------------------------------------------------------------
# Tool 1 (existing) — get_claim
# ---------------------------------------------------------------------------

def get_claim(claim_id: str) -> dict:
    claim = CLAIMS.get(claim_id)
    if claim is None:
        raise ToolError(f"No claim found for claim_id={claim_id!r}")
    return dict(claim)


GET_CLAIM_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_claim",
        "description": (
            "Retrieve the full claim record for a single claim_id from the "
            "claims system: policy_id, claimant_name, claimed_amount, "
            "incident_date, and the raw adjuster_notes text. This is the "
            "only tool that reads claim records. It never evaluates "
            "coverage/exclusions and never computes a payable amount."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "claim_id": {
                    "type": "string",
                    "description": "The claim identifier, e.g. 'CLM-2026-00001'.",
                }
            },
            "required": ["claim_id"],
        },
    },
}


# ---------------------------------------------------------------------------
# Tool 2 (existing) — check_policy_exclusions
# ---------------------------------------------------------------------------

def check_policy_exclusions(policy_id: str, cause: str) -> dict:
    if cause not in CAUSE_ENUM:
        raise ToolError(f"cause must be one of {CAUSE_ENUM}, got {cause!r}")
    policy = POLICIES.get(policy_id)
    if policy is None:
        raise ToolError(f"No policy found for policy_id={policy_id!r}")

    if cause == "unknown":
        return {
            "policy_id": policy_id,
            "cause": cause,
            "covered": False,
            "exclusion": None,
            "reason": "Cause of loss could not be determined; exclusion lookup cannot run without a cause.",
            "policy_excess": None,
            "sub_limit": None,
        }

    if not policy["active"]:
        rule = policy["exclusions"].get("policy_lapsed")
        return {
            "policy_id": policy_id,
            "cause": cause,
            "covered": False,
            "exclusion": "policy_lapsed",
            "reason": rule["reason"] if rule else "Policy is not active.",
            "policy_excess": policy["excess"],
            "sub_limit": None,
        }

    rule = policy["exclusions"].get(cause)
    if rule and rule.get("excluded"):
        return {
            "policy_id": policy_id,
            "cause": cause,
            "covered": False,
            "exclusion": cause,
            "reason": rule["reason"],
            "policy_excess": policy["excess"],
            "sub_limit": policy["sub_limits"].get(cause),
        }

    return {
        "policy_id": policy_id,
        "cause": cause,
        "covered": True,
        "exclusion": None,
        "reason": None,
        "policy_excess": policy["excess"],
        "sub_limit": policy["sub_limits"].get(cause),
    }


CHECK_POLICY_EXCLUSIONS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "check_policy_exclusions",
        "description": (
            "Look up policy terms for a given cause of loss: whether that "
            "cause is excluded, whether the policy is active, the policy's "
            "excess (deductible), and any sub-limit that caps payout for "
            "that cause. This is the only tool that makes a coverage/"
            "exclusion decision. It never reads claim records and never "
            "computes a payable amount."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "policy_id": {
                    "type": "string",
                    "description": "The policy identifier, e.g. 'POL-AUTO-1001'.",
                },
                "cause": {
                    "type": "string",
                    "enum": CAUSE_ENUM,
                    "description": "The cause of loss, read out of the adjuster notes (or 'unknown' if the notes do not state one).",
                },
            },
            "required": ["policy_id", "cause"],
        },
    },
}


# ---------------------------------------------------------------------------
# Tool 3 (NEW) — compute_payout
# ---------------------------------------------------------------------------

def compute_payout(claimed_amount: float, policy_excess: float | None,
                    claim_status: str, sub_limit: float | None = None) -> dict:
    if claim_status not in CLAIM_STATUS_ENUM:
        raise ToolError(f"claim_status must be one of {CLAIM_STATUS_ENUM}, got {claim_status!r}")

    if claim_status in ("DENIED", "PENDING_REVIEW"):
        return {"gross_amount": claimed_amount, "excess": policy_excess, "payable_amount": 0}

    base = claimed_amount if sub_limit is None else min(claimed_amount, sub_limit)
    excess_val = policy_excess or 0
    payable = max(0, base - excess_val)
    return {"gross_amount": claimed_amount, "excess": policy_excess, "payable_amount": payable}


COMPUTE_PAYOUT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "compute_payout",
        "description": (
            "Compute the payable amount for ONE claim from already-known "
            "facts: the claimed (gross) amount, the policy excess, the "
            "claim's status, and an optional sub-limit cap. This is the "
            "only tool that performs payout arithmetic. It never fetches "
            "claim data and never decides whether a cause is excluded — "
            "claim_status and sub_limit must already be decided before "
            "calling it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "claimed_amount": {
                    "type": "number",
                    "description": "The gross amount claimed, from the claim record.",
                },
                "policy_excess": {
                    "type": ["number", "null"],
                    "description": "The policy's excess/deductible, from check_policy_exclusions. Null if not yet known.",
                },
                "claim_status": {
                    "type": "string",
                    "enum": CLAIM_STATUS_ENUM,
                    "description": (
                        "APPROVED = covered, pay in full up to any sub_limit. "
                        "PARTIAL = covered but the claimed amount exceeds sub_limit. "
                        "DENIED = not covered, payable is 0. "
                        "PENDING_REVIEW = cause of loss undetermined, payable is 0 pending manual review."
                    ),
                },
                "sub_limit": {
                    "type": ["number", "null"],
                    "description": "Cap on the payable base amount for this cause, from check_policy_exclusions. Null if no cap applies.",
                },
            },
            "required": ["claimed_amount", "policy_excess", "claim_status"],
        },
    },
}


ALL_TOOL_SCHEMAS = [GET_CLAIM_SCHEMA, CHECK_POLICY_EXCLUSIONS_SCHEMA, COMPUTE_PAYOUT_SCHEMA]

TOOL_IMPLEMENTATIONS = {
    "get_claim": get_claim,
    "check_policy_exclusions": check_policy_exclusions,
    "compute_payout": compute_payout,
}


def execute_tool(name: str, arguments: dict) -> dict:
    fn = TOOL_IMPLEMENTATIONS.get(name)
    if fn is None:
        raise ToolError(f"Unknown tool: {name!r}")
    return fn(**arguments)
