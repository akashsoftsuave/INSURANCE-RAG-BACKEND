"""Week 10 Multi-Agent Orchestrator ('Claims Squad').

Decomposes a claim triage question, delegates to:
  1. `adjuster_notes_worker` (narrow tool: `get_claim` only)
  2. `coverage_exclusions_worker` (narrow tools: `get_policy`, `check_exclusions` only)
and synthesises the final structured claim decision using `compute_payout`.

Tracks every hand-off and its exact token count for `handoffs.log`, and
records real orchestrator behaviour when `coverage_exclusions_worker` returns
an injected HTTP 500 error.
"""

from __future__ import annotations

import json
import time

from groq import Groq

from app.core.config import settings
from app.claims_agent.contract import CONTRACT_FIELDS
from app.claims_agent.pricing import call_cost
from app.claims_agent.tools import (
    CLAIM_STATUS_ENUM,
    TASK_D_COMPUTE_PAYOUT_SCHEMA,
    ToolError,
    execute_tool,
)
from week10_multi_agent.workers import (
    DEFAULT_SEED,
    WorkerHTTPError,
    call_llm_with_backoff,
    _strip_json_fences,
    run_adjuster_notes_worker,
    run_coverage_exclusions_worker,
)

ORCHESTRATOR_DECOMPOSE_PROMPT = """You are the Lead Claims Orchestrator for an insurance claims squad.
You are given a `claim_id` to triage. You do not read raw claim records or policy exclusions yourself; you coordinate two specialized workers:
1. `adjuster_notes_worker`: fetches the claim record and summarizes the free-text adjuster notes into a structured cause of loss and claim metadata (`policy_id`, `claimed_amount`, `cause`, `notes_summary`).
2. `coverage_exclusions_worker`: takes ONLY `(policy_id, cause)` and checks policy terms (`get_policy`) and exclusions (`check_exclusions`).

Respond with ONLY a compact JSON decomposition plan (no markdown fences):
{
  "claim_id": "<claim_id>",
  "step_1_worker": "adjuster_notes_worker",
  "step_1_input": {"claim_id": "<claim_id>"},
  "step_2_worker": "coverage_exclusions_worker",
  "step_2_input_from_step_1": ["policy_id", "cause"],
  "context_policy": "scoped_no_raw_notes_resend"
}
"""

ORCHESTRATOR_SYNTHESIS_TOOLS = [TASK_D_COMPUTE_PAYOUT_SCHEMA]

ORCHESTRATOR_SYNTHESIS_PROMPT = f"""You are the Lead Claims Orchestrator synthesising the final claim decision from your two workers' structured reports.
You have ONE tool available (`compute_payout`) to perform the final payout arithmetic.

Strict synthesis rules (never round a conditional/excluded worker finding up to covered):
1. If `adjuster_notes_worker` reported `cause == "unknown"`:
   - `status` is `"PENDING_REVIEW"`, `covered` is `false`, `exclusion` is `null`, `excess` is `null`, `payable_amount` is `0`, and `gross_amount` is `claimed_amount`.
   - Call `compute_payout(claimed_amount=claimed_amount, policy_excess=null, claim_status="PENDING_REVIEW", sub_limit=null)`.
2. If `coverage_exclusions_worker` returned an error (such as HTTP 500 Internal Server Error) and could not complete the exclusions check:
   - NEVER lie or assume the claim is covered without a completed exclusions check.
   - Degrade safely to a partial/pending decision: `status` is `"PENDING_REVIEW"`, `covered` is `false`, `exclusion` is `null`, `gross_amount` is `claimed_amount`, `excess` is `null`, `payable_amount` is `0`, and state in `reason` that the coverage/exclusions worker failed with HTTP 500 after retry so coverage could not be verified.
3. Otherwise, use the exact findings from `coverage_exclusions_worker`:
   - If `covered` is `false` (an exclusion applies or the policy had lapsed): `status` is `"DENIED"`, `covered` is `false`, `exclusion` is the worker's `exclusion` code, `excess` is the worker's `excess`.
   - If `covered` is `true` and `sub_limit` is not null and `claimed_amount > sub_limit`: `status` is `"PARTIAL"`, `covered` is `true`, `exclusion` is `null`, `excess` is the worker's `excess`.
   - If `covered` is `true` and (`sub_limit` is null or `claimed_amount <= sub_limit`): `status` is `"APPROVED"`, `covered` is `true`, `exclusion` is `null`, `excess` is the worker's `excess`.
   - Always call `compute_payout(claimed_amount=..., policy_excess=..., claim_status=..., sub_limit=...)` to get `gross_amount`, `excess`, and `payable_amount`.
4. After calling `compute_payout`, respond with ONLY a JSON object (no markdown fences, no commentary) matching this exact contract:
{{
  "claim_id": "...",
  "status": one of {CLAIM_STATUS_ENUM},
  "covered": true/false,
  "exclusion": "<exclusion code>" or null,
  "gross_amount": <number>,
  "excess": <number> or null,
  "payable_amount": <number>,
  "reason": "<one or two sentence plain-English explanation>"
}}
"""


def run_orchestrator(
    claim_id: str,
    seed: int = DEFAULT_SEED,
    inject_500_on_exclusions: bool = False,
    max_worker_retries: int = 1,
    verbose: bool = False,
) -> dict:
    """Run the Multi-Agent Claims Squad Orchestrator over a single `claim_id`."""
    client = Groq(api_key=settings.GROQ_API_KEY, max_retries=0)
    wall_start = time.monotonic()
    total_paused_s = 0.0

    handoffs: list[dict] = []
    trajectory: list[dict] = []
    log_lines: list[str] = []

    def log(line: str) -> None:
        log_lines.append(line)
        if verbose:
            print(line)

    # ------------------------------------------------------------------
    # Hand-off 1: incoming_claim -> orchestrator_decompose
    # ------------------------------------------------------------------
    decomp_start = time.monotonic()
    decomp_messages = [
        {"role": "system", "content": ORCHESTRATOR_DECOMPOSE_PROMPT},
        {"role": "user", "content": f"Decompose triage task for claim_id: {claim_id}"},
    ]
    decomp_resp, paused = call_llm_with_backoff(
        client, decomp_messages, tools=None, seed=seed, tag=f"orchestrator_decompose:{claim_id}"
    )
    total_paused_s += paused
    decomp_usage = decomp_resp.usage
    d_p_tok = getattr(decomp_usage, "prompt_tokens", 0) or 0
    d_c_tok = getattr(decomp_usage, "completion_tokens", 0) or 0
    d_cost = call_cost(settings.MODEL_NAME, d_p_tok, d_c_tok)
    decomp_latency_ms = max(1, int((time.monotonic() - decomp_start - paused) * 1000))
    decomp_text = _strip_json_fences(decomp_resp.choices[0].message.content or "{}")
    try:
        decomp_plan = json.loads(decomp_text)
    except json.JSONDecodeError:
        decomp_plan = {"claim_id": claim_id, "context_policy": "scoped_no_raw_notes_resend"}

    handoffs.append({
        "claim_id": claim_id,
        "step": 1,
        "handoff": "orchestrator -> decompose_plan",
        "from": "user_request",
        "to": "orchestrator_planner",
        "payload_summary": f"claim_id={claim_id}",
        "prompt_tokens": d_p_tok,
        "completion_tokens": d_c_tok,
        "total_tokens": d_p_tok + d_c_tok,
        "cost_usd": d_cost,
        "latency_ms": decomp_latency_ms,
        "status": "ok",
    })
    log(f"[orchestrator] handoff='orchestrator -> decompose_plan' tokens={d_p_tok + d_c_tok} plan={decomp_plan}")

    # ------------------------------------------------------------------
    # Hand-off 2: orchestrator -> adjuster_notes_worker
    # ------------------------------------------------------------------
    notes_res = run_adjuster_notes_worker(claim_id, client=client, seed=seed, verbose=verbose)
    for step in notes_res["trajectory"]:
        trajectory.append({"order": len(trajectory) + 1, **step})
    log_lines.extend(notes_res["log"])

    notes_out = notes_res["output"] or {}
    policy_id = notes_out.get("policy_id", "UNKNOWN")
    cause = notes_out.get("cause", "unknown")
    claimed_amount = notes_out.get("claimed_amount", 0)

    handoffs.append({
        "claim_id": claim_id,
        "step": 2,
        "handoff": "orchestrator -> adjuster_notes_worker",
        "from": "orchestrator",
        "to": "adjuster_notes_worker",
        "payload_summary": f"claim_id={claim_id} -> extracted policy_id={policy_id}, cause={cause}",
        "prompt_tokens": notes_res["prompt_tokens"],
        "completion_tokens": notes_res["completion_tokens"],
        "total_tokens": notes_res["total_tokens"],
        "cost_usd": notes_res["cost_usd"],
        "latency_ms": notes_res["latency_ms"],
        "status": "ok",
    })
    log(
        f"[orchestrator] handoff='orchestrator -> adjuster_notes_worker' "
        f"tokens={notes_res['total_tokens']} output={notes_out}"
    )

    # ------------------------------------------------------------------
    # Hand-off 3: orchestrator -> coverage_exclusions_worker
    # Scoped context ONLY: (policy_id, cause). Raw adjuster_notes never re-sent.
    # ------------------------------------------------------------------
    exclusions_out = None
    exclusions_error = None
    exclusions_attempts = 0
    exclusions_p_tok = 0
    exclusions_c_tok = 0
    exclusions_cost = 0.0
    exclusions_latency_ms = 0

    for attempt in range(1, max_worker_retries + 2):
        exclusions_attempts = attempt
        attempt_start = time.monotonic()
        try:
            exc_res = run_coverage_exclusions_worker(
                policy_id=policy_id,
                cause=cause,
                client=client,
                seed=seed,
                inject_500=inject_500_on_exclusions,
                verbose=verbose,
            )
            exclusions_p_tok += exc_res["prompt_tokens"]
            exclusions_c_tok += exc_res["completion_tokens"]
            exclusions_cost += exc_res["cost_usd"]
            exclusions_latency_ms += exc_res["latency_ms"]
            exclusions_out = exc_res["output"]
            for step in exc_res["trajectory"]:
                trajectory.append({"order": len(trajectory) + 1, **step})
            log_lines.extend(exc_res["log"])
            break
        except WorkerHTTPError as err:
            err_ms = max(1, int((time.monotonic() - attempt_start) * 1000))
            exclusions_latency_ms += err_ms
            exclusions_error = {
                **err.to_dict(),
                "attempt": attempt,
                "max_attempts": max_worker_retries + 1,
            }
            log(
                f"[orchestrator] handoff='orchestrator -> coverage_exclusions_worker' "
                f"attempt={attempt}/{max_worker_retries + 1} ERROR={err.status_code} {err.message}"
            )
            if attempt <= max_worker_retries:
                log(f"[orchestrator] retrying coverage_exclusions_worker (retry {attempt}/{max_worker_retries})...")

    handoffs.append({
        "claim_id": claim_id,
        "step": 3,
        "handoff": "orchestrator -> coverage_exclusions_worker",
        "from": "orchestrator",
        "to": "coverage_exclusions_worker",
        "payload_summary": (
            f"scoped={{'policy_id': '{policy_id}', 'cause': '{cause}'}} "
            f"(raw adjuster_notes omitted)"
        ),
        "prompt_tokens": exclusions_p_tok,
        "completion_tokens": exclusions_c_tok,
        "total_tokens": exclusions_p_tok + exclusions_c_tok,
        "cost_usd": exclusions_cost,
        "latency_ms": exclusions_latency_ms,
        "status": "error_500" if exclusions_error else "ok",
        "attempts": exclusions_attempts,
        "error": exclusions_error,
    })

    # ------------------------------------------------------------------
    # Hand-off 4: workers -> orchestrator_synthesis
    # ------------------------------------------------------------------
    synth_start = time.monotonic()
    synth_paused_s = 0.0
    synth_p_tok = 0
    synth_c_tok = 0
    synth_cost = 0.0

    synthesis_input = {
        "claim_id": claim_id,
        "adjuster_notes_worker_report": notes_out,
        "coverage_exclusions_worker_report": exclusions_out if exclusions_error is None else {
            "worker_status": "FAILED",
            "http_status": 500,
            "attempts": exclusions_attempts,
            "error": exclusions_error["error"],
        },
    }

    synth_messages = [
        {"role": "system", "content": ORCHESTRATOR_SYNTHESIS_PROMPT},
        {
            "role": "user",
            "content": (
                "Synthesise the final claim decision from these worker reports "
                f"(call compute_payout first, then emit the final JSON): {json.dumps(synthesis_input)}"
            ),
        },
    ]

    final_output = None
    termination_reason = "completed"
    payout_called = False

    for _step in range(4):
        active_tools = ORCHESTRATOR_SYNTHESIS_TOOLS if not payout_called else None
        resp, paused = call_llm_with_backoff(
            client,
            synth_messages,
            tools=active_tools,
            seed=seed,
            tag=f"orchestrator_synthesis:{claim_id}",
        )
        synth_paused_s += paused
        total_paused_s += paused

        usage = resp.usage
        p_tok = getattr(usage, "prompt_tokens", 0) or 0
        c_tok = getattr(usage, "completion_tokens", 0) or 0
        synth_p_tok += p_tok
        synth_c_tok += c_tok
        synth_cost += call_cost(settings.MODEL_NAME, p_tok, c_tok)

        msg = resp.choices[0].message
        if msg.tool_calls:
            synth_messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            })
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                try:
                    result = execute_tool(tc.function.name, args)
                    if tc.function.name == "compute_payout":
                        payout_called = True
                except ToolError as err:
                    result = {"error": str(err)}
                log(f"[orchestrator_synthesis] tool_call={tc.function.name} args={args} -> {result}")
                trajectory.append({
                    "order": len(trajectory) + 1,
                    "worker": "orchestrator_synthesis",
                    "tool": tc.function.name,
                    "arguments": args,
                    "result": result,
                })
                synth_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })
            continue

        raw = _strip_json_fences(msg.content or "")
        try:
            parsed = json.loads(raw)
            if all(f in parsed for f in CONTRACT_FIELDS):
                final_output = parsed
                termination_reason = (
                    "degraded_worker_500" if exclusions_error else "completed"
                )
            else:
                termination_reason = "invalid_output_schema"
                log(f"[orchestrator_synthesis] missing contract fields: {raw}")
        except json.JSONDecodeError:
            termination_reason = "invalid_output_json"
            log(f"[orchestrator_synthesis] invalid JSON: {raw}")
        break

    synth_latency_ms = max(1, int((time.monotonic() - synth_start - synth_paused_s) * 1000))
    handoffs.append({
        "claim_id": claim_id,
        "step": 4,
        "handoff": "workers -> orchestrator_synthesis",
        "from": "adjuster_notes_worker + coverage_exclusions_worker",
        "to": "orchestrator_synthesis",
        "payload_summary": (
            f"claimed_amount={claimed_amount}, cause={cause}, "
            f"exclusions_status={'HTTP_500' if exclusions_error else 'ok'}"
        ),
        "prompt_tokens": synth_p_tok,
        "completion_tokens": synth_c_tok,
        "total_tokens": synth_p_tok + synth_c_tok,
        "cost_usd": synth_cost,
        "latency_ms": synth_latency_ms,
        "status": "degraded_partial" if exclusions_error else "ok",
    })

    wall_ms = sum(int(h["latency_ms"]) for h in handoffs)
    tokens_total = sum(h["total_tokens"] for h in handoffs)
    cost_total = sum(h["cost_usd"] for h in handoffs)

    return {
        "claim_id": claim_id,
        "output": final_output,
        "termination_reason": termination_reason,
        "trajectory": trajectory,
        "tool_sequence": [step["tool"] for step in trajectory],
        "handoffs": handoffs,
        "iterations": len(handoffs),
        "tokens_total": tokens_total,
        "cost_total": cost_total,
        "wall_clock_ms": wall_ms,
        "worker_failure_injected": inject_500_on_exclusions,
        "worker_error": exclusions_error,
        "log": log_lines,
    }
