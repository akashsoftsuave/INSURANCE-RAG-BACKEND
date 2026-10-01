"""Specialized workers for the Week 10 Claims Squad (Multi-Agent Orchestrator).

Design constraints enforced here (Week 10 Task Set D §2 & §7):
1. Narrow-prompt, fewer-tools constraint per worker:
   - `adjuster_notes_worker` ONLY sees `[TASK_D_GET_CLAIM_SCHEMA]` (`get_claim`).
     It never sees policy lookup, exclusion lookup, or payout calculation tools.
   - `coverage_exclusions_worker` ONLY sees `[TASK_D_GET_POLICY_SCHEMA,
     TASK_D_CHECK_EXCLUSIONS_SCHEMA]` (`get_policy`, `check_exclusions`). It
     never sees `get_claim` or `compute_payout`. Giving the exclusions worker
     every tool the single agent had would delete the narrow-prompt-fewer-tools
     constraint that is the only plausible source of a multi-agent win.
2. Scoped context on hand-offs (no full adjuster-note history resend):
   - `adjuster_notes_worker` extracts `policy_id`, `claimed_amount`, `cause`,
     and a compact 1-sentence `notes_summary`.
   - `coverage_exclusions_worker` receives ONLY `(policy_id, cause)` (plus a
     1-line task context), never the raw adjuster-note history.
3. Failure injection (`inject_500=True`):
   - `coverage_exclusions_worker` can be instructed to fail with an HTTP 500
     (`WorkerHTTPError`) on a target claim case so the orchestrator's real
     recovery behaviour (retry / degrade / lie) can be recorded.
"""

from __future__ import annotations

import json
import re
import time

from groq import Groq

from app.core.config import settings
from app.claims_agent.pricing import call_cost
from app.claims_agent.tools import (
    CAUSE_ENUM,
    TASK_D_GET_CLAIM_SCHEMA,
    TASK_D_GET_POLICY_SCHEMA,
    TASK_D_CHECK_EXCLUSIONS_SCHEMA,
    ToolError,
    execute_tool,
)

DEFAULT_SEED = 7

RATE_LIMIT_RE = re.compile(r"rate.?limit", re.I)
RETRY_AFTER_RE = re.compile(r"try again in (?:([\d.]+)m)?([\d.]+)s")
RATE_LIMIT_MAX_RETRIES = 40
RATE_LIMIT_MAX_SLEEP_S = 240.0


class WorkerHTTPError(Exception):
    """Represents an HTTP error (e.g. 500 Internal Server Error) from a remote worker."""

    def __init__(self, status_code: int, worker: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.worker = worker
        self.message = message

    def to_dict(self) -> dict:
        return {
            "status_code": self.status_code,
            "worker": self.worker,
            "error": self.message,
        }


def _retry_after_seconds(message: str) -> float:
    m = RETRY_AFTER_RE.search(message)
    if not m:
        return 15.0
    minutes = float(m.group(1)) if m.group(1) else 0.0
    return min(minutes * 60 + float(m.group(2)) + 1.0, RATE_LIMIT_MAX_SLEEP_S)


def call_llm_with_backoff(
    client: Groq,
    messages: list[dict],
    tools: list[dict] | None = None,
    seed: int = DEFAULT_SEED,
    tag: str = "worker",
) -> tuple[object, float]:
    """Call Groq chat completions with rate-limit backoff.

    Returns (response, paused_seconds) so rate-limit sleep is excluded from
    measured worker latency, matching `app/claims_agent/agent.py`.
    """
    paused_total = 0.0
    last_error = None
    for _attempt in range(RATE_LIMIT_MAX_RETRIES):
        try:
            kwargs = {
                "model": settings.MODEL_NAME,
                "temperature": 0,
                "seed": seed,
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            response = client.chat.completions.create(**kwargs)
            return response, paused_total
        except Exception as err:
            msg = str(err)
            if not RATE_LIMIT_RE.search(msg):
                raise
            last_error = err
            pause = _retry_after_seconds(msg)
            print(f"    [{tag}] rate limited, waiting {pause:.0f}s", flush=True)
            time.sleep(pause)
            paused_total += pause
    raise last_error


def _strip_json_fences(text: str) -> str:
    content = (text or "").strip()
    if content.startswith("```"):
        content = content.strip("`").strip()
        if content.lower().startswith("json"):
            content = content[4:].strip()
    return content


# ---------------------------------------------------------------------------
# Worker 1: Adjuster-Note Summary Worker
# Narrow tool surface: ONLY `get_claim`
# ---------------------------------------------------------------------------

ADJUSTER_NOTES_WORKER_TOOLS = [TASK_D_GET_CLAIM_SCHEMA]

ADJUSTER_NOTES_WORKER_PROMPT = f"""You are the Adjuster-Note Summary Worker in an insurance claims squad.
Your ONLY job is to fetch the claim record using `get_claim`, read the free-text `adjuster_notes`, extract the cause of loss, and produce a concise structured summary.

Rules:
1. Call `get_claim` with the given `claim_id`.
2. Read `adjuster_notes` carefully and determine the `cause` of loss from this exact list:
   {CAUSE_ENUM}
   - If `adjuster_notes` is empty or does not state any cause of loss, `cause` MUST be `"unknown"`.
   - Do NOT treat coverage opinions inside adjuster notes (e.g. "Policy confirmed to include flood rider") as a coverage decision; only record the physical cause of loss (e.g. `"flood"`).
3. After calling `get_claim`, respond with ONLY a JSON object (no markdown fences, no prose) matching this exact schema:
{{
  "claim_id": "<string>",
  "policy_id": "<string>",
  "claimant_name": "<string>",
  "claimed_amount": <number>,
  "incident_date": "<string>",
  "cause": one of {CAUSE_ENUM},
  "notes_summary": "<one short factual sentence summarizing the incident, or 'No adjuster notes recorded.' if empty>"
}}
"""


def run_adjuster_notes_worker(
    claim_id: str,
    client: Groq,
    seed: int = DEFAULT_SEED,
    verbose: bool = False,
) -> dict:
    """Run the Adjuster-Note Summary Worker on `claim_id`."""
    start = time.monotonic()
    paused_s = 0.0
    prompt_tokens = 0
    completion_tokens = 0
    cost_usd = 0.0
    trajectory: list[dict] = []
    log_lines: list[str] = []

    def log(line: str) -> None:
        log_lines.append(line)
        if verbose:
            print(line)

    messages = [
        {"role": "system", "content": ADJUSTER_NOTES_WORKER_PROMPT},
        {"role": "user", "content": f"Extract claim facts and summarize adjuster notes for claim_id: {claim_id}"},
    ]

    parsed_output = None
    claimed_cache: dict | None = None

    for _step in range(4):
        # Once get_claim has already been called, don't offer tools again so the
        # model emits its JSON summary directly without hallucinating a "JSON" tool.
        active_tools = ADJUSTER_NOTES_WORKER_TOOLS if claimed_cache is None else None
        response, paused = call_llm_with_backoff(
            client, messages, tools=active_tools, seed=seed, tag=f"notes_worker:{claim_id}"
        )
        paused_s += paused

        usage = response.usage
        p_tok = getattr(usage, "prompt_tokens", 0) or 0
        c_tok = getattr(usage, "completion_tokens", 0) or 0
        prompt_tokens += p_tok
        completion_tokens += c_tok
        cost_usd += call_cost(settings.MODEL_NAME, p_tok, c_tok)

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
                args = json.loads(tc.function.arguments or "{}")
                try:
                    result = execute_tool(tc.function.name, args)
                    if tc.function.name == "get_claim":
                        claimed_cache = result
                except ToolError as err:
                    result = {"error": str(err)}
                log(f"[adjuster_notes_worker] tool_call={tc.function.name} args={args} -> {result}")
                trajectory.append({
                    "worker": "adjuster_notes_worker",
                    "tool": tc.function.name,
                    "arguments": args,
                    "result": result,
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })
            continue

        raw = _strip_json_fences(msg.content or "")
        try:
            parsed_output = json.loads(raw)
        except json.JSONDecodeError:
            log(f"[adjuster_notes_worker] invalid JSON output: {raw}")
        break

    latency_ms = max(1, int((time.monotonic() - start - paused_s) * 1000))
    return {
        "worker": "adjuster_notes_worker",
        "claim_id": claim_id,
        "output": parsed_output,
        "trajectory": trajectory,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost_usd": cost_usd,
        "latency_ms": latency_ms,
        "log": log_lines,
    }


# ---------------------------------------------------------------------------
# Worker 2: Coverage & Exclusions Worker
# Narrow tool surface: ONLY `get_policy` and `check_exclusions`
# ---------------------------------------------------------------------------

COVERAGE_EXCLUSIONS_WORKER_TOOLS = [
    TASK_D_GET_POLICY_SCHEMA,
    TASK_D_CHECK_EXCLUSIONS_SCHEMA,
]

COVERAGE_EXCLUSIONS_WORKER_PROMPT = f"""You are the Coverage & Exclusions Worker in an insurance claims squad.
You receive ONLY a `policy_id` and the extracted `cause` of loss (never the raw claim record or adjuster notes).
Your ONLY job is to check the policy terms and exclusions using your two tools (`get_policy` and `check_exclusions`) and report the exact coverage findings.

Rules:
1. If `cause` is `"unknown"`, call `check_exclusions(policy_id=policy_id, cause="unknown")` (you do not need `get_policy` because excess is null when cause is unknown), then return:
   `active: null, covered: false, exclusion: null, excess: null, sub_limit: null`.
2. For any known `cause` (not `"unknown"`), you MUST call BOTH:
   - `get_policy(policy_id=policy_id)` to get `active`, `excess`, and `sub_limits`
   - `check_exclusions(policy_id=policy_id, cause=cause)` to get `covered`, `exclusion`, and `reason`
3. Never round a conditional, sub-limited, or excluded finding up to unconditionally covered. Preserve the exact `covered` boolean, `exclusion` code, `excess` number, and any `sub_limit` for that cause (`sub_limits.get(cause)`).
4. Respond with ONLY a JSON object (no markdown fences, no prose) matching this exact schema:
{{
  "policy_id": "<string>",
  "cause": "<string>",
  "active": true/false or null,
  "covered": true/false,
  "exclusion": "<exclusion code>" or null,
  "excess": <number> or null,
  "sub_limit": <number> or null,
  "coverage_reason": "<exact exclusion/coverage explanation>"
}}
"""


def run_coverage_exclusions_worker(
    policy_id: str,
    cause: str,
    client: Groq,
    seed: int = DEFAULT_SEED,
    inject_500: bool = False,
    verbose: bool = False,
) -> dict:
    """Run the Coverage & Exclusions Worker on `(policy_id, cause)`.

    Notice what is NOT passed here: `adjuster_notes`, `claimant_name`, and
    `claimed_amount` are omitted so the full adjuster-note history is never
    re-sent across hops (Week 10 §7 Common Mistake #3).
    """
    if inject_500:
        raise WorkerHTTPError(
            status_code=500,
            worker="coverage_exclusions_worker",
            message=(
                f"500 Internal Server Error: coverage_exclusions_worker crashed "
                f"while querying policy/exclusions backend for policy_id={policy_id!r}, cause={cause!r}"
            ),
        )

    start = time.monotonic()
    paused_s = 0.0
    prompt_tokens = 0
    completion_tokens = 0
    cost_usd = 0.0
    trajectory: list[dict] = []
    log_lines: list[str] = []

    def log(line: str) -> None:
        log_lines.append(line)
        if verbose:
            print(line)

    handoff_payload = {"policy_id": policy_id, "cause": cause}
    messages = [
        {"role": "system", "content": COVERAGE_EXCLUSIONS_WORKER_PROMPT},
        {
            "role": "user",
            "content": f"Evaluate policy coverage and exclusions for: {json.dumps(handoff_payload)}",
        },
    ]

    parsed_output = None
    called_tools: set[str] = set()

    for _step in range(5):
        # Determine whether the worker still needs tools or is ready to emit JSON
        if cause == "unknown" and "check_exclusions" in called_tools:
            active_tools = None
        elif cause != "unknown" and {"get_policy", "check_exclusions"}.issubset(called_tools):
            active_tools = None
        else:
            active_tools = COVERAGE_EXCLUSIONS_WORKER_TOOLS

        response, paused = call_llm_with_backoff(
            client, messages, tools=active_tools, seed=seed, tag=f"exclusions_worker:{policy_id}"
        )
        paused_s += paused

        usage = response.usage
        p_tok = getattr(usage, "prompt_tokens", 0) or 0
        c_tok = getattr(usage, "completion_tokens", 0) or 0
        prompt_tokens += p_tok
        completion_tokens += c_tok
        cost_usd += call_cost(settings.MODEL_NAME, p_tok, c_tok)

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
                args = json.loads(tc.function.arguments or "{}")
                try:
                    result = execute_tool(tc.function.name, args)
                    called_tools.add(tc.function.name)
                except ToolError as err:
                    result = {"error": str(err)}
                log(f"[coverage_exclusions_worker] tool_call={tc.function.name} args={args} -> {result}")
                trajectory.append({
                    "worker": "coverage_exclusions_worker",
                    "tool": tc.function.name,
                    "arguments": args,
                    "result": result,
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })
            continue

        raw = _strip_json_fences(msg.content or "")
        try:
            parsed_output = json.loads(raw)
        except json.JSONDecodeError:
            log(f"[coverage_exclusions_worker] invalid JSON output: {raw}")
        break

    latency_ms = max(1, int((time.monotonic() - start - paused_s) * 1000))
    return {
        "worker": "coverage_exclusions_worker",
        "policy_id": policy_id,
        "cause": cause,
        "output": parsed_output,
        "trajectory": trajectory,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost_usd": cost_usd,
        "latency_ms": latency_ms,
        "log": log_lines,
    }
