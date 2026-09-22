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


# ===========================================================================
# Week 8 (Task Set D) — the tool surface the trajectory evaluation runs on.
#
# Why this set exists
# -------------------
# In the Week-7 set above, check_policy_exclusions returns the coverage
# DECISION *and* the policy's excess and sub-limit. That one tool therefore
# owns two unrelated jobs, and it makes the interesting agent failure mode
# physically impossible: compute_payout needs an excess, the excess only comes
# out of the exclusion check, so the agent can never reach a payout without
# having checked exclusions. The baseline scores 100% trajectory not because
# the agent plans well but because no wrong path exists to take.
#
# This set splits that tool along its real seam, which is also the split the
# Task Set D brief assumes (get_claim / get_policy / get_exclusions /
# calculate_payout):
#
#   get_claim        -> the claim record
#   get_policy       -> the policy's TERMS: active flag, excess, sub-limits.
#                       Makes no coverage decision.
#   check_exclusions -> the coverage DECISION for one cause. Returns no money.
#   compute_payout   -> arithmetic only.
#
# Two consequences, both wanted:
#   1. get_policy and check_exclusions each need only the policy_id, so they
#      can legitimately be called in EITHER order. Cases therefore have
#      genuinely alternate valid paths rather than one forced sequence.
#   2. get_claim -> get_policy -> compute_payout is now reachable. It skips the
#      coverage decision entirely and still lands on the right payout whenever
#      the claim happens to be clean — the right-answer-wrong-path case.
#
# The Week-7 names and behaviour above are untouched, so run_race.py,
# workflow.py and every Week-7 result stay exactly as they were.
# ===========================================================================

def get_policy(policy_id: str) -> dict:
    """Policy terms only. Deliberately returns no covered/excluded verdict."""
    policy = POLICIES.get(policy_id)
    if policy is None:
        raise ToolError(f"No policy found for policy_id={policy_id!r}")
    return {
        "policy_id": policy["policy_id"],
        "product": policy["product"],
        "active": policy["active"],
        "excess": policy["excess"],
        "sub_limits": dict(policy["sub_limits"]),
    }


def check_exclusions(policy_id: str, cause: str) -> dict:
    """Coverage decision only. Deliberately returns no excess and no
    sub-limit, so a payout can never be assembled out of this tool alone."""
    if cause not in CAUSE_ENUM:
        raise ToolError(f"cause must be one of {CAUSE_ENUM}, got {cause!r}")
    policy = POLICIES.get(policy_id)
    if policy is None:
        raise ToolError(f"No policy found for policy_id={policy_id!r}")

    if cause == "unknown":
        return {
            "policy_id": policy_id, "cause": cause, "covered": False, "exclusion": None,
            "reason": "Cause of loss could not be determined; exclusion lookup cannot run without a cause.",
        }

    if not policy["active"]:
        rule = policy["exclusions"].get("policy_lapsed")
        return {
            "policy_id": policy_id, "cause": cause, "covered": False,
            "exclusion": "policy_lapsed",
            "reason": rule["reason"] if rule else "Policy is not active.",
        }

    rule = policy["exclusions"].get(cause)
    if rule and rule.get("excluded"):
        return {
            "policy_id": policy_id, "cause": cause, "covered": False,
            "exclusion": cause, "reason": rule["reason"],
        }

    return {
        "policy_id": policy_id, "cause": cause, "covered": True,
        "exclusion": None, "reason": None,
    }


# --- baseline schemas ------------------------------------------------------
# Plain, accurate, neutral descriptions: each says what the tool returns and
# nothing about when to call it or what has to come first. This is what an
# ordinary first-pass tool definition looks like, and it is the "before" side
# of the Week 8 mitigation.

TASK_D_GET_CLAIM_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_claim",
        "description": (
            "Returns the claim record for a claim_id: policy_id, "
            "claimant_name, claimed_amount, incident_date, and the free-text "
            "adjuster_notes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "claim_id": {"type": "string", "description": "The claim identifier, e.g. 'CLM-2026-00001'."},
            },
            "required": ["claim_id"],
        },
    },
}

TASK_D_GET_POLICY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_policy",
        "description": (
            "Returns a policy's terms: the product, whether the policy is "
            "active, the excess (deductible), and any per-cause sub-limits."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "policy_id": {"type": "string", "description": "The policy identifier, e.g. 'POL-AUTO-1001'."},
            },
            "required": ["policy_id"],
        },
    },
}

TASK_D_CHECK_EXCLUSIONS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "check_exclusions",
        "description": (
            "Returns whether a given cause of loss is excluded under a "
            "policy, along with the exclusion code and its wording if one "
            "applies."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "policy_id": {"type": "string", "description": "The policy identifier, e.g. 'POL-AUTO-1001'."},
                "cause": {
                    "type": "string",
                    "enum": CAUSE_ENUM,
                    "description": "The cause of loss.",
                },
            },
            "required": ["policy_id", "cause"],
        },
    },
}

TASK_D_COMPUTE_PAYOUT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "compute_payout",
        "description": (
            "Returns gross_amount, excess and payable_amount for a claim, "
            "given the claimed amount, the policy excess, the claim status "
            "and an optional sub-limit."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "claimed_amount": {"type": "number", "description": "The gross amount claimed."},
                "policy_excess": {"type": ["number", "null"], "description": "The policy's excess/deductible."},
                "claim_status": {
                    "type": "string",
                    "enum": CLAIM_STATUS_ENUM,
                    "description": (
                        "APPROVED = covered, pay in full up to any sub_limit. "
                        "PARTIAL = covered but the claimed amount exceeds sub_limit. "
                        "DENIED = not covered, payable is 0. "
                        "PENDING_REVIEW = cause of loss undetermined, payable is 0."
                    ),
                },
                "sub_limit": {"type": ["number", "null"], "description": "Cap on the payable base amount for this cause."},
            },
            "required": ["claimed_amount", "policy_excess", "claim_status"],
        },
    },
}

TASK_D_TOOL_SCHEMAS = [
    TASK_D_GET_CLAIM_SCHEMA,
    TASK_D_GET_POLICY_SCHEMA,
    TASK_D_CHECK_EXCLUSIONS_SCHEMA,
    TASK_D_COMPUTE_PAYOUT_SCHEMA,
]

TOOL_IMPLEMENTATIONS["get_policy"] = get_policy
TOOL_IMPLEMENTATIONS["check_exclusions"] = check_exclusions

# --- mitigated schemas -----------------------------------------------------
# WEEK 8 §7 — THE ONE MITIGATION: "tighter tool description".
#
# Targets the measured top failure mode, skipped_required_step. In the baseline
# the agent reached a correct answer while never calling compute_payout: it did
# get_claim -> get_policy -> check_exclusions, then did the subtraction in its
# head. Nothing in the baseline descriptions says a tool is mandatory or that
# one must precede another — they only say what each tool returns — so skipping
# one is not visibly wrong from where the agent stands.
#
# The change is a STRING EDIT to four `description` fields and nothing else.
# Same four tools, same names, same parameter schemas, same implementations,
# same system prompt, same budgets, same seed, same loop. That isolation is
# what makes the before/after comparison mean something.
#
# Deliberately NOT done (each would be a second mitigation):
#   - no argument validation / precondition enforcement in code
#   - no hard step limit
#   - no re-planning or reflection step
#   - no swap to the deterministic workflow
# Nothing here can force the agent's hand; it only tells the truth about the
# tools' contract. The agent can still ignore it, which is why the after-run is
# measured rather than assumed.

_MITIGATION_NOTE_GET_CLAIM = (
    "Returns the claim record for a claim_id: policy_id, claimant_name, "
    "claimed_amount, incident_date, and the free-text adjuster_notes. "
    "REQUIRED FIRST STEP for every claim: policy_id and claimed_amount exist "
    "nowhere else, and the cause of loss has to be read out of adjuster_notes."
)

_MITIGATION_NOTE_GET_POLICY = (
    "Returns a policy's terms: the product, whether the policy is active, the "
    "excess (deductible), and any per-cause sub-limits. REQUIRED before "
    "compute_payout: the excess and sub-limit it returns are the ONLY "
    "legitimate source for those two numbers. Never infer an excess or a "
    "sub-limit from the claim record, from the adjuster notes, or from what is "
    "typical for the product."
)

_MITIGATION_NOTE_CHECK_EXCLUSIONS = (
    "Returns whether a given cause of loss is excluded under a policy, along "
    "with the exclusion code and its wording if one applies. REQUIRED for "
    "every claim whose cause of loss can be determined, including claims that "
    "look obviously covered. Coverage is a property of the policy wording, not "
    "of the claim: an active policy does not imply cover, and adjuster notes "
    "that appear to settle coverage (for example 'policy confirmed to include "
    "the flood rider') are the adjuster's opinion, not a coverage decision. "
    "Never set covered/exclusion without calling this tool."
)

_MITIGATION_NOTE_COMPUTE_PAYOUT = (
    "Returns gross_amount, excess and payable_amount for a claim, given the "
    "claimed amount, the policy excess, the claim status and an optional "
    "sub-limit. REQUIRED LAST STEP for every claim you answer with a payable "
    "figure, and the only place a payable_amount may be produced. Do not do "
    "the arithmetic yourself — not even when the result is obviously zero "
    "because the claim is denied, and not even when it is a single "
    "subtraction. PRECONDITIONS: claimed_amount must be the value get_claim "
    "returned; policy_excess and sub_limit must be the values get_policy "
    "returned; claim_status must follow the coverage decision check_exclusions "
    "returned."
)


def _with_description(schema: dict, description: str) -> dict:
    """Copy a tool schema with only its description replaced, so the mitigation
    provably cannot touch the name, the parameters or the implementation."""
    return {
        **schema,
        "function": {**schema["function"], "description": description},
    }


TASK_D_MITIGATED_TOOL_SCHEMAS = [
    _with_description(TASK_D_GET_CLAIM_SCHEMA, _MITIGATION_NOTE_GET_CLAIM),
    _with_description(TASK_D_GET_POLICY_SCHEMA, _MITIGATION_NOTE_GET_POLICY),
    _with_description(TASK_D_CHECK_EXCLUSIONS_SCHEMA, _MITIGATION_NOTE_CHECK_EXCLUSIONS),
    _with_description(TASK_D_COMPUTE_PAYOUT_SCHEMA, _MITIGATION_NOTE_COMPUTE_PAYOUT),
]

# Guard-rail on the mitigation itself: assert that nothing except the
# description text differs between the two sets. If a future edit changes a
# parameter schema or a tool name, this fails loudly rather than quietly
# invalidating the before/after comparison.
for _base, _mit in zip(TASK_D_TOOL_SCHEMAS, TASK_D_MITIGATED_TOOL_SCHEMAS):
    assert _base["function"]["name"] == _mit["function"]["name"]
    assert _base["function"]["parameters"] == _mit["function"]["parameters"]
    assert _base["function"]["description"] != _mit["function"]["description"]
del _base, _mit

TOOL_SETS = {
    "week7": ALL_TOOL_SCHEMAS,
    "task_d": TASK_D_TOOL_SCHEMAS,
}

MITIGATED_TOOL_SETS = {
    "task_d": TASK_D_MITIGATED_TOOL_SCHEMAS,
}
