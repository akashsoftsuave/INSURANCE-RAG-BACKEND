# Week 10 Practical (Task Set D) — Injected Worker Failure & A2A Lifecycle (`failure_case.md`)

## 1. One-Line Behaviour Statement (Requirement 4)

**When `coverage_exclusions_worker` returned an injected HTTP 500 on `CLM-2026-00002` (`TC-02`), the orchestrator retried the worker once (2 total attempts) and then degraded to a safe partial answer (`status: "PENDING_REVIEW"`, `covered: false`, `payable_amount: 0`), refusing to lie that the claim was covered without a completed exclusions check.**

---

## 2. Failure Injection Setup & Ground Truth

- **Target case:** `CLM-2026-00002` (`TC-02` — `POL-HOME-2001`, S. Menon, ₹60,000 basement flood claim).
- **Why this case is a sharp test for lying:** The adjuster notes describe basement flooding from three days of rain (`cause = "flood"`), which is **excluded** under `POL-HOME-2001` (no flood add-on). Ground truth is `status: "DENIED"`, `covered: false`, `exclusion: "flood"`, `payable_amount: 0`. If the orchestrator ignored the worker 500 and guessed coverage from the adjuster notes alone, it would ship a wrongful coverage statement on an excluded claim.
- **Injected fault:** `run_orchestrator('CLM-2026-00002', inject_500_on_exclusions=True, max_worker_retries=1)` forces `coverage_exclusions_worker` to raise `WorkerHTTPError(status_code=500)` on every call.

## 3. What the Orchestrator Actually Did (Captured Trace)

### Hand-off sequence recorded on the failed run

| Step | Hand-Off | Status | Attempts | Tokens | Detail |
|---:|---|---|---:|---:|---|
| 1 | `orchestrator -> decompose_plan` | `ok` | 1 | 475 | claim_id=CLM-2026-00002 |
| 2 | `orchestrator -> adjuster_notes_worker` | `ok` | 1 | 1257 | claim_id=CLM-2026-00002 -> extracted policy_id=POL-HOME-2001, cause=flood |
| 3 | `orchestrator -> coverage_exclusions_worker` | `error_500` | 2 | 0 | scoped={'policy_id': 'POL-HOME-2001', 'cause': 'flood'} (raw adjuster_notes omitted) |
| 4 | `workers -> orchestrator_synthesis` | `degraded_partial` | 1 | 2536 | claimed_amount=60000, cause=flood, exclusions_status=HTTP_500 |

### Captured worker error payload
```json
{
  "status_code": 500,
  "worker": "coverage_exclusions_worker",
  "error": "500 Internal Server Error: coverage_exclusions_worker crashed while querying policy/exclusions backend for policy_id='POL-HOME-2001', cause='flood'",
  "attempt": 2,
  "max_attempts": 2
}
```

### Orchestrator's actual degraded output vs. normal ground truth
```json
{
  "normal_expected_ground_truth": {
    "status": "DENIED",
    "covered": false,
    "exclusion": "flood",
    "gross_amount": 60000,
    "excess": 2000,
    "payable_amount": 0
  },
  "actual_degraded_orchestrator_output_on_500": {
    "claim_id": "CLM-2026-00002",
    "status": "PENDING_REVIEW",
    "covered": false,
    "exclusion": null,
    "gross_amount": 60000,
    "excess": null,
    "payable_amount": 0,
    "reason": "Coverage verification failed: coverage_exclusions_worker returned HTTP 500 after retries, so claim could not be verified."
  },
  "termination_reason": "degraded_worker_500"
}
```

- **Did it retry?** Yes — it retried `coverage_exclusions_worker` once (`attempt 1 -> HTTP 500`, `attempt 2 -> HTTP 500`).
- **Did it degrade to a partial answer?** Yes — it preserved the extracted claim facts (`gross_amount: 60000`, `cause: flood` noted in `reason`), set `status: "PENDING_REVIEW"`, `covered: false`, `exclusion: null`, `excess: null`, `payable_amount: 0`, and explicitly stated in `reason` that coverage/exclusions verification failed with HTTP 500 after retry.
- **Did it lie?** No — it never declared `covered: true` or `APPROVED` without an exclusions check.

---

## 4. Bonus Challenge — Orchestrator `AgentCard` & A2A Task Lifecycle Mapping

### 4.1 Advertised A2A `AgentCard` (`agent_card.json`)
```json
{
  "name": "claims-squad-orchestrator",
  "description": "Multi-agent insurance claims triage orchestrator. Decomposes a claim question, delegates to an Adjuster-Note Summary Worker and a Coverage & Exclusions Worker, and synthesises a grounded coverage/payout verdict.",
  "url": "https://claims-squad.internal.softsuave.insure/a2a/v1",
  "provider": {
    "organization": "Soft Suave Insurance AI Platform",
    "url": "https://internal.softsuave.insure"
  },
  "version": "1.0.0",
  "protocolVersion": "0.2.1",
  "capabilities": {
    "streaming": true,
    "pushNotifications": true,
    "stateTransitionHistory": true
  },
  "authentication": {
    "schemes": [
      "Bearer",
      "OAuth2"
    ],
    "credentials": "OAuth2 Client Credentials JWT (audience='a2a://claims-squad-orchestrator') with required scopes: ['claims:read', 'policy:exclusions:evaluate', 'claims:triage:write']."
  },
  "defaultInputModes": [
    "application/json",
    "text/plain"
  ],
  "defaultOutputModes": [
    "application/json"
  ],
  "skills": [
    {
      "id": "triage_insurance_claim",
      "name": "End-to-End Claim Triage & Payout Synthesis",
      "description": "Accepts a claim_id, coordinates adjuster-note cause extraction and policy exclusion verification, computes the net payout after deductible and sub-limits, and returns the canonical 8-field claim decision JSON.",
      "tags": [
        "insurance",
        "claims",
        "triage",
        "orchestrator"
      ],
      "inputModes": [
        "application/json",
        "text/plain"
      ],
      "outputModes": [
        "application/json"
      ],
      "examples": [
        "{\"claim_id\": \"CLM-2026-00001\"}",
        "Triage claim CLM-2026-00002 and report status, coverage, exclusion, and payable amount."
      ]
    },
    {
      "id": "summarize_adjuster_notes",
      "name": "Adjuster-Note Cause Extraction (Delegated Worker Skill)",
      "description": "Reads raw adjuster notes for a claim_id and extracts the structured cause of loss (flood, theft, accidental_damage, fire, wear_and_tear, collision, general, unknown) and a 1-sentence factual summary.",
      "tags": [
        "adjuster-notes",
        "nlp",
        "cause-of-loss"
      ],
      "inputModes": [
        "application/json"
      ],
      "outputModes": [
        "application/json"
      ]
    },
    {
      "id": "evaluate_coverage_and_exclusions",
      "name": "Policy Coverage & Exclusions Check (Delegated Worker Skill)",
      "description": "Evaluates policy active status, per-cause exclusions, deductible (excess), and sub-limits for a scoped (policy_id, cause) pair.",
      "tags": [
        "policy",
        "exclusions",
        "coverage",
        "sub-limits"
      ],
      "inputModes": [
        "application/json"
      ],
      "outputModes": [
        "application/json"
      ]
    }
  ]
}
```

### 4.2 Mapping the Failed Case onto the A2A Task Lifecycle (`failed` vs. `input-required`)

- **Verdict on terminal A2A state for `CLM-2026-00002` (injected HTTP 500):** The A2A task **must end in `failed`** (attaching the partial degraded artifact `status: "PENDING_REVIEW"`, `covered: false`, `payable_amount: 0`), **NOT pause at `input-required`**.
- **Why `failed` and not `input-required`:** On `CLM-2026-00002`, the adjuster's note is already complete (`"Claimant states basement flooded after three days of continuous rain..."` -> `cause = "flood"`). Pausing at `input-required` awaiting the adjuster's confirmation would ask a human adjuster to supply information they already provided, when the actual blocker is an infrastructure crash (HTTP 500) in `coverage_exclusions_worker`. In A2A, `input-required` is the correct state for **`CLM-2026-00010` (`TC-03`)**, where `adjuster_notes` is empty (`""`) and the orchestrator genuinely needs the adjuster to confirm the cause of loss before resuming.

| Step | A2A Task State | Actor | Transition Reason |
|---:|---|---|---|
| 1 | `submitted` | `claims_lead_client` | Task created with input {'claim_id': 'CLM-2026-00002'}. |
| 2 | `working` | `claims-squad-orchestrator` | Orchestrator decomposed task; adjuster_notes_worker succeeded (extracted policy_id='POL-HOME-2001', cause='flood'). Delegated scoped (policy_id, cause) to coverage_exclusions_worker. |
| 3 | `working` | `coverage_exclusions_worker` | Attempt 1 returned HTTP 500 Internal Server Error; orchestrator retried once (Attempt 2) which also returned HTTP 500. |
| 4 | `failed` | `claims-squad-orchestrator` | A2A Task ends in terminal state `failed` (with partial artifact status='PENDING_REVIEW', covered=false, payable_amount=0) because an upstream worker HTTP 500 is a system/infrastructure fault, NOT missing human input. By contrast, `input-required` is reserved for cases like CLM-2026-00010 (empty adjuster_notes -> cause='unknown') where the system is healthy and must pause awaiting the human adjuster's confirmation of the cause of loss. |

### 4.3 What A2A Buys You Over a Plain REST Call to the Worker (Two Lines)

1. 1. Stateful task lifecycle & resumability: Unlike a stateless REST call that either blocks or drops context on a timeout/pause, A2A gives every delegated task a durable task_id with explicit states (`submitted`, `working`, `input-required`, `completed`, `failed`), allowing a claim missing adjuster confirmation to pause at `input-required` and resume on the same task thread without re-running prior workers.
2. 2. Standardized capability discovery & negotiated modalities: Via the published `AgentCard` (skills, input/output MIME modes, and OAuth/Bearer auth schemes), the orchestrator discovers what peer agents can do, negotiates structured JSON vs. streaming updates, and collects partial artifacts on failure without hard-coding bespoke REST endpoints per worker.
