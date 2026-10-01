"""Bonus Challenge (Week 10 §5): A2A AgentCard & Task Lifecycle Mapping.

1. Publishes the `AgentCard` that the Claims Squad Orchestrator advertises
   (skills, defaultInputModes, defaultOutputModes, authentication).
2. Maps the injected worker failure case (`CLM-2026-00002`, HTTP 500 on
   `coverage_exclusions_worker`) and the missing-notes case (`CLM-2026-00010`)
   onto the A2A task lifecycle (`submitted` -> `working` -> `failed` vs
   `input-required`).
3. Provides the two-line statement of what A2A buys over a plain REST call to
   the worker.
"""

from __future__ import annotations

ORCHESTRATOR_AGENT_CARD = {
    "name": "claims-squad-orchestrator",
    "description": (
        "Multi-agent insurance claims triage orchestrator. Decomposes a claim "
        "question, delegates to an Adjuster-Note Summary Worker and a Coverage & "
        "Exclusions Worker, and synthesises a grounded coverage/payout verdict."
    ),
    "url": "https://claims-squad.internal.softsuave.insure/a2a/v1",
    "provider": {
        "organization": "Soft Suave Insurance AI Platform",
        "url": "https://internal.softsuave.insure",
    },
    "version": "1.0.0",
    "protocolVersion": "0.2.1",
    "capabilities": {
        "streaming": True,
        "pushNotifications": True,
        "stateTransitionHistory": True,
    },
    "authentication": {
        "schemes": ["Bearer", "OAuth2"],
        "credentials": (
            "OAuth2 Client Credentials JWT (audience='a2a://claims-squad-orchestrator') "
            "with required scopes: ['claims:read', 'policy:exclusions:evaluate', 'claims:triage:write']."
        ),
    },
    "defaultInputModes": ["application/json", "text/plain"],
    "defaultOutputModes": ["application/json"],
    "skills": [
        {
            "id": "triage_insurance_claim",
            "name": "End-to-End Claim Triage & Payout Synthesis",
            "description": (
                "Accepts a claim_id, coordinates adjuster-note cause extraction and "
                "policy exclusion verification, computes the net payout after deductible "
                "and sub-limits, and returns the canonical 8-field claim decision JSON."
            ),
            "tags": ["insurance", "claims", "triage", "orchestrator"],
            "inputModes": ["application/json", "text/plain"],
            "outputModes": ["application/json"],
            "examples": [
                '{"claim_id": "CLM-2026-00001"}',
                "Triage claim CLM-2026-00002 and report status, coverage, exclusion, and payable amount.",
            ],
        },
        {
            "id": "summarize_adjuster_notes",
            "name": "Adjuster-Note Cause Extraction (Delegated Worker Skill)",
            "description": (
                "Reads raw adjuster notes for a claim_id and extracts the structured "
                "cause of loss (flood, theft, accidental_damage, fire, wear_and_tear, "
                "collision, general, unknown) and a 1-sentence factual summary."
            ),
            "tags": ["adjuster-notes", "nlp", "cause-of-loss"],
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
        },
        {
            "id": "evaluate_coverage_and_exclusions",
            "name": "Policy Coverage & Exclusions Check (Delegated Worker Skill)",
            "description": (
                "Evaluates policy active status, per-cause exclusions, deductible "
                "(excess), and sub-limits for a scoped (policy_id, cause) pair."
            ),
            "tags": ["policy", "exclusions", "coverage", "sub-limits"],
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
        },
    ],
}


def map_failure_to_a2a_lifecycle(claim_id: str, failure_result: dict) -> dict:
    """Map the injected HTTP 500 worker failure onto the A2A Task Lifecycle."""
    return {
        "task_id": f"a2a-task-{claim_id.lower()}-500",
        "claim_id": claim_id,
        "state_transitions": [
            {
                "state": "submitted",
                "actor": "claims_lead_client",
                "detail": f"Task created with input {{'claim_id': '{claim_id}'}}.",
            },
            {
                "state": "working",
                "actor": "claims-squad-orchestrator",
                "detail": (
                    "Orchestrator decomposed task; adjuster_notes_worker succeeded "
                    "(extracted policy_id='POL-HOME-2001', cause='flood'). Delegated "
                    "scoped (policy_id, cause) to coverage_exclusions_worker."
                ),
            },
            {
                "state": "working",
                "actor": "coverage_exclusions_worker",
                "detail": (
                    "Attempt 1 returned HTTP 500 Internal Server Error; orchestrator "
                    "retried once (Attempt 2) which also returned HTTP 500."
                ),
            },
            {
                "state": "failed",
                "actor": "claims-squad-orchestrator",
                "detail": (
                    "A2A Task ends in terminal state `failed` (with partial artifact "
                    "status='PENDING_REVIEW', covered=false, payable_amount=0) because "
                    "an upstream worker HTTP 500 is a system/infrastructure fault, NOT "
                    "missing human input. By contrast, `input-required` is reserved for "
                    "cases like CLM-2026-00010 (empty adjuster_notes -> cause='unknown') "
                    "where the system is healthy and must pause awaiting the human "
                    "adjuster's confirmation of the cause of loss."
                ),
            },
        ],
        "should_end_state": "failed",
        "why_failed_not_input_required": (
            "The injected HTTP 500 on CLM-2026-00002 should end in `failed` (while "
            "emitting a safe degraded artifact `PENDING_REVIEW`, `covered=false`, "
            "`payable_amount=0`), NOT `input-required`, because the adjuster notes "
            "were already complete (`cause='flood'`) and the human adjuster cannot "
            "fix a crashed exclusions worker by typing confirmation. `input-required` "
            "belongs to CLM-2026-00010 (empty adjuster notes), where the task pauses "
            "awaiting the adjuster's incident description to resume."
        ),
        "what_a2a_buys_over_rest_two_lines": [
            "1. Stateful task lifecycle & resumability: Unlike a stateless REST call that either blocks or drops context on a timeout/pause, A2A gives every delegated task a durable task_id with explicit states (`submitted`, `working`, `input-required`, `completed`, `failed`), allowing a claim missing adjuster confirmation to pause at `input-required` and resume on the same task thread without re-running prior workers.",
            "2. Standardized capability discovery & negotiated modalities: Via the published `AgentCard` (skills, input/output MIME modes, and OAuth/Bearer auth schemes), the orchestrator discovers what peer agents can do, negotiates structured JSON vs. streaming updates, and collects partial artifacts on failure without hard-coding bespoke REST endpoints per worker.",
        ],
    }
