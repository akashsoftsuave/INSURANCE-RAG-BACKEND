from __future__ import annotations

import json
import re
import time

from groq import Groq

from app.core.config import settings
from app.claims_agent.budgets import BudgetConfig, BudgetTracker
from app.claims_agent.pricing import call_cost
from app.claims_agent.tools import (TOOL_SETS, MITIGATED_TOOL_SETS, execute_tool,
                                    ToolError, CAUSE_ENUM, CLAIM_STATUS_ENUM)
from app.claims_agent.contract import CONTRACT_FIELDS
from app.claims_agent.context_window import apply_sliding_window

# Some providers reject a tool call naming a tool that was never offered,
# with a 400 rather than letting it through. That is not a transport fault —
# it is the agent inventing a tool (most often a fictitious "json" tool it
# tries to "call" to emit its final answer). Detected here so the fabricated
# call is recorded as a real trajectory step instead of being lost inside a
# generic model error.
HALLUCINATED_TOOL_RE = re.compile(
    r"attempted to call tool '([^']+)' which was not in request")

# A provider rate limit is a transport condition, not agent behaviour, and it
# carries its own "try again in 2m0.96s" hint. Retried here rather than at the
# harness level so a 429 arriving at iteration 4 does not throw away the three
# iterations already paid for — which matters when the daily token budget is
# what is scarce. Nothing about the agent's decisions changes: the same
# messages are re-sent and the paused seconds are excluded from the wall-clock
# budget via BudgetTracker.add_pause().
RATE_LIMIT_RE = re.compile(r"rate.?limit", re.I)
RETRY_AFTER_RE = re.compile(r"try again in (?:([\d.]+)m)?([\d.]+)s")
RATE_LIMIT_MAX_RETRIES = 40
RATE_LIMIT_MAX_SLEEP_S = 240.0


def _retry_after_seconds(message: str) -> float:
    m = RETRY_AFTER_RE.search(message)
    if not m:
        return 30.0
    minutes = float(m.group(1)) if m.group(1) else 0.0
    return min(minutes * 60 + float(m.group(2)) + 1.0, RATE_LIMIT_MAX_SLEEP_S)

# Fixed seed forwarded to the provider so repeated evaluation runs are as
# reproducible as the provider allows (Week 8 §11 reproducibility).
DEFAULT_SEED = 7

SYSTEM_PROMPT = f"""You are a claims-triage agent. You are given a claim_id.
Use the available tools to gather facts, then respond with ONLY a JSON
object (no markdown fences, no commentary) matching exactly this schema:

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

Business rules you must apply yourself when choosing tool arguments and the
final status:
- The claim's adjuster_notes is free text. Read it and determine the cause
  of loss yourself; it is not given to you as a separate field. Valid cause
  values are: {CAUSE_ENUM}. If the notes are empty or do not describe a
  cause, the cause is "unknown".
- If cause is "unknown": do not guess. The claim's status is
  PENDING_REVIEW, covered=false, exclusion=null, payable_amount=0, and
  excess should be null (do not call check_policy_exclusions or
  compute_payout in this case — you already have everything you need).
- Otherwise, call check_policy_exclusions with the policy_id and the cause
  you determined, to find out whether it's covered, any exclusion, the
  policy excess, and any sub_limit.
- If not covered: status is DENIED, payable_amount is 0.
- If covered and a sub_limit applies and claimed_amount exceeds it: status
  is PARTIAL.
- If covered and no sub_limit applies, or claimed_amount is within it:
  status is APPROVED.
- Always call compute_payout to get the final gross_amount/excess/
  payable_amount numbers rather than computing them yourself — pass it the
  claim_status you've decided and the sub_limit you found.
- Never call a tool for a purpose outside its stated single job.
"""


# ---------------------------------------------------------------------------
# Week 8 (Task Set D) — the planning prompt the trajectory evaluation uses.
#
# Why a second prompt exists at all:
# SYSTEM_PROMPT above is a Week-7 artifact. It was written to win a race
# against a deterministic workflow, so it dictates the tool-call sequence in
# prose — "call check_policy_exclusions with the policy_id and the cause you
# determined", "Always call compute_payout ... rather than computing them
# yourself", "do not call check_policy_exclusions or compute_payout in this
# case". With those lines in place the agent is a script with an LLM reading
# it out: the first Task-D baseline scored 10/10 outcome AND 10/10 trajectory,
# a 0 percentage-point gap and zero failure modes. There is nothing to measure,
# because the procedural contract has already been mitigated inside the prompt.
#
# PLANNING_SYSTEM_PROMPT keeps every OUTCOME rule identical — the same output
# schema, the same cause-reading rule, the same status rules, the same payout
# arithmetic — and removes only the sentences that pick the tools and fix their
# order. The agent is told what a correct answer is and left to plan how to get
# there, which is the only configuration in which "trajectory evaluation" means
# anything.
#
# It is used for BOTH the before and after phases of the Task-D evaluation, so
# it cannot flatter the mitigation. SYSTEM_PROMPT is still the default, so
# run_race.py and every Week-7 artifact behave exactly as before.
# ---------------------------------------------------------------------------

PLANNING_SYSTEM_PROMPT = f"""You are a claims-triage agent. You are given a
claim_id. Work out the claim's outcome using the tools available to you, then
respond with ONLY a JSON object (no markdown fences, no commentary) matching
exactly this schema:

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

What a correct outcome looks like:
- The cause of loss is not a field anywhere. It is described in the claim's
  free-text adjuster_notes and has to be read out of them. The valid cause
  values are: {CAUSE_ENUM}. If the notes are empty, or describe no cause, the
  cause is "unknown".
- Cause "unknown": do not guess a cause. status is PENDING_REVIEW,
  covered=false, exclusion=null, excess=null, payable_amount=0.
- A cause the policy excludes, or a policy that was not in force at the time
  of loss: status is DENIED, covered=false, payable_amount=0, and exclusion is
  the exclusion code that applied.
- Covered, and the claimed amount is within any cap that applies to that
  cause: status is APPROVED.
- Covered, but the claimed amount exceeds the cap that applies to that cause:
  status is PARTIAL.
- gross_amount is the amount claimed. excess is the policy's excess
  (deductible). payable_amount is the covered base amount — capped where a cap
  applies — minus the excess, and never below zero.

Decide for yourself which tools to use and in what order.
"""


PROMPTS = {
    "week7": SYSTEM_PROMPT,
    "planning": PLANNING_SYSTEM_PROMPT,
}


def run_agent(claim_id: str, budget_config: BudgetConfig | None = None, verbose: bool = False,
              use_context_window: bool = False, seed: int = DEFAULT_SEED,
              mitigation: bool = False, prompt_mode: str = "week7",
              tool_set: str = "week7") -> dict:
    """Run the claims-triage agent over one claim.

    `seed` is forwarded to the provider for reproducibility (Week 8 §11).
    The returned dict now also carries a structured `trajectory`: one entry
    per tool call with its execution order, arguments, result, and latency.
    This is pure instrumentation — it records what the agent already did and
    never influences a decision.

    `mitigation` is the Week 8 §7 A/B switch and the ONLY behavioural
    difference between the "before" and "after" evaluation runs. False =
    the agent exactly as it was; True = the single mitigation described in
    eval/mitigation.py. Everything else — prompt, budgets, seed,
    temperature, loop structure — is identical in both phases.

    `prompt_mode` selects the operating instruction: "week7" (the default,
    unchanged, used by run_race.py) or "planning" (the task-level prompt the
    Week 8 trajectory evaluation uses in both of its phases).

    `tool_set` selects the tool surface: "week7" (the default, unchanged
    3-tool set) or "task_d" (the 4-tool set with get_policy split out of the
    exclusion check — see the long note at the bottom of tools.py).
    """
    if prompt_mode not in PROMPTS:
        raise ValueError(f"prompt_mode must be one of {sorted(PROMPTS)}, got {prompt_mode!r}")
    if tool_set not in TOOL_SETS:
        raise ValueError(f"tool_set must be one of {sorted(TOOL_SETS)}, got {tool_set!r}")
    # max_retries=0: the SDK's own 429 retries would sleep on top of the
    # backoff below, double-counting the wait and hiding it from the log.
    # The rate-limit handling in the loop is the single owner of that policy.
    client = Groq(api_key=settings.GROQ_API_KEY, max_retries=0)
    tracker = BudgetTracker(config=budget_config or BudgetConfig())
    trajectory: list[dict] = []
    tool_schemas = TOOL_SETS[tool_set]
    if mitigation:
        tool_schemas = MITIGATED_TOOL_SETS.get(tool_set, tool_schemas)

    messages = [
        {"role": "system", "content": PROMPTS[prompt_mode]},
        {"role": "user", "content": f"claim_id: {claim_id}"},
    ]

    log_lines: list[str] = []

    def log(line: str) -> None:
        log_lines.append(line)
        if verbose:
            print(line)

    wall_start = time.monotonic()
    termination_reason = None
    final_output = None

    for _safety in range(50):  # hard backstop; real bound is the budget check below
        reason = tracker.check()
        if reason:
            termination_reason = reason
            log(f"[agent] BUDGET_TERMINATED")
            log(f"[agent] reason={reason}")
            snap = tracker.snapshot()
            log(f"[agent] tokens_total={snap['tokens_total']}")
            log(f"[agent] max_tokens={tracker.config.max_tokens}")
            log(f"[agent] iterations={snap['iterations']}")
            log(f"[agent] cost_total={snap['cost_total']}")
            log(f"[agent] elapsed_ms={snap['elapsed_ms']}")
            log(f"[agent] final_status=terminated")
            break

        try:
            response = None
            last_error = None
            for _attempt in range(RATE_LIMIT_MAX_RETRIES):
                try:
                    response = client.chat.completions.create(
                        model=settings.MODEL_NAME,
                        temperature=0,
                        seed=seed,
                        messages=messages,
                        tools=tool_schemas,
                        tool_choice="auto",
                    )
                    break
                except Exception as call_error:
                    message = str(call_error)
                    if not RATE_LIMIT_RE.search(message):
                        raise
                    last_error = call_error
                    pause = _retry_after_seconds(message)
                    # Printed unconditionally, not only under verbose: a run
                    # parked behind a daily token cap is otherwise
                    # indistinguishable from a hung process.
                    print(f"    [{claim_id}] rate limited, waiting {pause:.0f}s", flush=True)
                    log(f"[agent] RATE_LIMITED waiting {pause:.0f}s")
                    time.sleep(pause)
                    tracker.add_pause(pause)
                    wall_start += pause
            if response is None:
                raise last_error
        except Exception as e:
            message = str(e)
            invented = HALLUCINATED_TOOL_RE.search(message)
            if invented:
                # The agent tried to call a tool that does not exist. Record
                # it as the step it was, so the trajectory shows the wrong
                # tool rather than a blank gap.
                termination_reason = "hallucinated_tool"
                trajectory.append({
                    "order": len(trajectory) + 1,
                    "iteration": tracker.snapshot()["iterations"],
                    "tool": invented.group(1),
                    "arguments": {},
                    "result": {"error": message},
                    "error": message,
                    "latency_ms": 0,
                    "iteration_tokens": 0,
                    "iteration_cost": 0.0,
                    "hallucinated": True,
                })
                log(f"[agent] HALLUCINATED_TOOL {invented.group(1)!r} — "
                    f"rejected by the provider, agent produced no answer")
            else:
                termination_reason = "model_error"
                log(f"[agent] MODEL_ERROR {e}")
            break

        usage = response.usage
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        cost = call_cost(settings.MODEL_NAME, prompt_tokens, completion_tokens)
        tracker.record_iteration(prompt_tokens, completion_tokens, cost)
        iteration_tokens = prompt_tokens + completion_tokens
        iteration_cost = cost

        snap = tracker.snapshot()
        log(f"[agent] iteration={snap['iterations']}")
        log(f"[agent] tokens_total={snap['tokens_total']}")
        log(f"[agent] cost_total={snap['cost_total']}")
        log(f"[agent] elapsed_ms={snap['elapsed_ms']}")

        reason = tracker.check()
        if reason:
            termination_reason = reason
            log(f"[agent] BUDGET_TERMINATED")
            log(f"[agent] reason={reason}")
            snap = tracker.snapshot()
            log(f"[agent] tokens_total={snap['tokens_total']}")
            log(f"[agent] max_tokens={tracker.config.max_tokens}")
            log(f"[agent] iterations={snap['iterations']}")
            log(f"[agent] cost_total={snap['cost_total']}")
            log(f"[agent] elapsed_ms={snap['elapsed_ms']}")
            log(f"[agent] final_status=terminated")
            break

        msg = response.choices[0].message

        if msg.tool_calls:
            messages.append({
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
                step_start = time.monotonic()
                step_error = None
                args = None
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    result = execute_tool(tc.function.name, args)
                    if use_context_window and tc.function.name == "get_claim" and "adjuster_notes" in result:
                        windowed, usage = apply_sliding_window(result["adjuster_notes"], client, settings.MODEL_NAME)
                        result = dict(result, adjuster_notes=windowed)
                        if usage:
                            tracker.add_side_cost(usage["tokens"], usage["cost"])
                            log(f"[agent] context_window applied original_turns={usage['original_turns']} "
                                f"summarized_turns={usage['summarized_turns']} kept_recent_turns={usage['kept_recent_turns']} "
                                f"tokens={usage['tokens']} cost={usage['cost']:.6f}")
                    log(f"[agent] tool_call={tc.function.name} args={args} -> {result}")
                except ToolError as e:
                    result = {"error": str(e)}
                    step_error = str(e)
                    log(f"[agent] tool_call={tc.function.name} args={tc.function.arguments} -> ERROR {e}")
                except (TypeError, json.JSONDecodeError) as e:
                    # Malformed / unexpected arguments from the model. Recorded
                    # rather than raised so the trajectory keeps the bad step.
                    result = {"error": f"{type(e).__name__}: {e}"}
                    step_error = f"{type(e).__name__}: {e}"
                    log(f"[agent] tool_call={tc.function.name} args={tc.function.arguments} -> ERROR {e}")
                trajectory.append({
                    "order": len(trajectory) + 1,
                    "iteration": snap["iterations"],
                    "tool": tc.function.name,
                    "arguments": args if args is not None else {"__raw__": tc.function.arguments},
                    "result": result,
                    "error": step_error,
                    "latency_ms": int((time.monotonic() - step_start) * 1000),
                    "iteration_tokens": iteration_tokens,
                    "iteration_cost": iteration_cost,
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })
            continue

        # No tool calls -> this is the final answer.
        content = (msg.content or "").strip()
        try:
            if content.startswith("```"):
                content = content.strip("`")
                if content.startswith("json"):
                    content = content[4:]
            parsed = json.loads(content)
            if all(f in parsed for f in CONTRACT_FIELDS):
                final_output = parsed
                termination_reason = "completed"
            else:
                termination_reason = "invalid_output_schema"
                log(f"[agent] final message missing required fields: {content}")
        except json.JSONDecodeError:
            termination_reason = "invalid_output_json"
            log(f"[agent] final message was not valid JSON: {content}")
        break
    else:
        termination_reason = termination_reason or "max_iterations"

    wall_ms = int((time.monotonic() - wall_start) * 1000)
    snap = tracker.snapshot()

    return {
        "claim_id": claim_id,
        "output": final_output,
        "termination_reason": termination_reason,
        "trajectory": trajectory,
        "tool_sequence": [step["tool"] for step in trajectory],
        "mitigation": mitigation,
        "prompt_mode": prompt_mode,
        "tool_set": tool_set,
        "steps_taken": len(trajectory),
        "seed": seed,
        "iterations": snap["iterations"],
        "tokens_total": snap["tokens_total"],
        "cost_total": snap["cost_total"],
        "wall_clock_ms": wall_ms,
        "log": log_lines,
    }
