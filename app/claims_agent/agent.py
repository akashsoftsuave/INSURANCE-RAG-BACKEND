from __future__ import annotations

import json
import time

from groq import Groq

from app.core.config import settings
from app.claims_agent.budgets import BudgetConfig, BudgetTracker
from app.claims_agent.pricing import call_cost
from app.claims_agent.tools import ALL_TOOL_SCHEMAS, execute_tool, ToolError, CAUSE_ENUM, CLAIM_STATUS_ENUM
from app.claims_agent.contract import CONTRACT_FIELDS
from app.claims_agent.context_window import apply_sliding_window

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


def run_agent(claim_id: str, budget_config: BudgetConfig | None = None, verbose: bool = False,
              use_context_window: bool = False) -> dict:
    client = Groq(api_key=settings.GROQ_API_KEY)
    tracker = BudgetTracker(config=budget_config or BudgetConfig())

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
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
            response = client.chat.completions.create(
                model=settings.MODEL_NAME,
                temperature=0,
                messages=messages,
                tools=ALL_TOOL_SCHEMAS,
                tool_choice="auto",
            )
        except Exception as e:
            termination_reason = "model_error"
            log(f"[agent] MODEL_ERROR {e}")
            break

        usage = response.usage
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        cost = call_cost(settings.MODEL_NAME, prompt_tokens, completion_tokens)
        tracker.record_iteration(prompt_tokens, completion_tokens, cost)

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
                    log(f"[agent] tool_call={tc.function.name} args={tc.function.arguments} -> ERROR {e}")
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
        "iterations": snap["iterations"],
        "tokens_total": snap["tokens_total"],
        "cost_total": snap["cost_total"],
        "wall_clock_ms": wall_ms,
        "log": log_lines,
    }
