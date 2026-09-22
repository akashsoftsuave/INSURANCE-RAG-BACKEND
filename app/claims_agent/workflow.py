from __future__ import annotations

import time

from groq import Groq

from app.core.config import settings
from app.claims_agent.pricing import call_cost
from app.claims_agent.tools import get_claim, check_policy_exclusions, compute_payout, ToolError
from app.claims_agent.contract import make_output
from app.claims_agent.context_window import apply_sliding_window

CAUSE_KEYWORDS = [
    ("flood", ["flood", "monsoon", "waterlog", "heavy rain", "rainwater", "rising water"]),
    ("theft", ["stolen", "theft", "burglar", "broken into", "break-in"]),
    ("wear_and_tear", ["wear and tear", "rust", "corrosion", "gradual"]),
    ("fire", ["fire", "electrical short", "burnt", "burned", "smoke damage"]),
    ("accidental_damage", ["dropped", "cracked screen", "accidental"]),
    ("collision", ["collision", "bumper", "traffic signal", "another vehicle"]),
]


def extract_cause(notes: str) -> str:
    """Explicit, hard-coded business rule: map adjuster notes text to one
    of the controlled cause values. No model call, no branching agent —
    plain keyword matching, same logic for every claim."""
    text = (notes or "").strip().lower()
    if not text:
        return "unknown"
    for cause, keywords in CAUSE_KEYWORDS:
        if any(kw in text for kw in keywords):
            return cause
    return "general"


def decide_status(covered: bool, claimed_amount: float, sub_limit: float | None) -> str:
    """Explicit business rule for the status field. Same logic the agent is
    instructed to apply itself in its system prompt."""
    if not covered:
        return "DENIED"
    if sub_limit is not None and claimed_amount > sub_limit:
        return "PARTIAL"
    return "APPROVED"


REASON_PROMPT_TEMPLATE = """You are a claims-triage assistant. Write a one or
two sentence plain-English explanation of the following already-decided
claims outcome. Do not change any of the facts below, do not add new facts,
just explain them.

claim_id: {claim_id}
status: {status}
covered: {covered}
exclusion: {exclusion}
cause_of_loss: {cause}
gross_amount: {gross_amount}
excess: {excess}
payable_amount: {payable_amount}

Respond with ONLY the explanation sentence(s), no preamble."""


def run_workflow(claim_id: str, verbose: bool = False, use_context_window: bool = False) -> dict:
    client = Groq(api_key=settings.GROQ_API_KEY)
    wall_start = time.monotonic()
    log_lines: list[str] = []
    side_tokens = 0
    side_cost = 0.0

    def log(line: str) -> None:
        log_lines.append(line)
        if verbose:
            print(line)

    # Step 1: get claim (fixed, always first)
    try:
        claim = get_claim(claim_id)
    except ToolError as e:
        wall_ms = int((time.monotonic() - wall_start) * 1000)
        return {
            "claim_id": claim_id, "output": None, "termination_reason": f"error:{e}",
            "iterations": 0, "tokens_total": 0, "cost_total": 0.0, "wall_clock_ms": wall_ms,
            "log": [f"[workflow] {e}"],
        }
    log(f"[workflow] step=1 get_claim -> policy_id={claim['policy_id']} claimed_amount={claim['claimed_amount']}")

    # Step 2: read adjuster notes (fixed, always second)
    notes = claim["adjuster_notes"]
    if use_context_window:
        windowed, usage = apply_sliding_window(notes, client, settings.MODEL_NAME)
        notes = windowed
        if usage:
            side_tokens += usage["tokens"]
            side_cost += usage["cost"]
            log(f"[workflow] context_window applied original_turns={usage['original_turns']} "
                f"summarized_turns={usage['summarized_turns']} kept_recent_turns={usage['kept_recent_turns']} "
                f"tokens={usage['tokens']} cost={usage['cost']:.6f}")
    log(f"[workflow] step=2 adjuster_notes={notes!r}")

    # Step 3: extract cause (explicit business rule, not an LLM judgment)
    cause = extract_cause(notes)
    log(f"[workflow] step=3 extract_cause -> {cause}")

    if cause == "unknown":
        # Fixed branch, not a dynamic tool choice: explicit business rule.
        status, covered, exclusion = "PENDING_REVIEW", False, None
        gross_amount, excess, payable_amount = claim["claimed_amount"], None, 0
        log("[workflow] step=4 cause unknown -> status=PENDING_REVIEW, skipping exclusion/payout steps")
    else:
        # Step 4: check policy exclusions (fixed, always fourth when cause is known)
        excl = check_policy_exclusions(claim["policy_id"], cause)
        log(f"[workflow] step=4 check_policy_exclusions -> covered={excl['covered']} exclusion={excl['exclusion']} "
            f"policy_excess={excl['policy_excess']} sub_limit={excl['sub_limit']}")

        # Step 5: decide status, then compute payout (fixed order, explicit rule)
        status = decide_status(excl["covered"], claim["claimed_amount"], excl["sub_limit"])
        payout = compute_payout(claim["claimed_amount"], excl["policy_excess"], status, excl["sub_limit"])
        log(f"[workflow] step=5 decide_status -> {status}; compute_payout -> {payout}")

        covered = excl["covered"]
        exclusion = excl["exclusion"]
        gross_amount, excess, payable_amount = payout["gross_amount"], payout["excess"], payout["payable_amount"]

    # Step 6: single fixed model call to render the reason text. No tools
    # bound, no branching on its output -- it fills exactly one text field.
    prompt = REASON_PROMPT_TEMPLATE.format(
        claim_id=claim_id, status=status, covered=covered, exclusion=exclusion,
        cause=cause, gross_amount=gross_amount, excess=excess, payable_amount=payable_amount,
    )
    response = client.chat.completions.create(
        model=settings.MODEL_NAME,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    usage = response.usage
    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
    cost = call_cost(settings.MODEL_NAME, prompt_tokens, completion_tokens)
    tokens_total = prompt_tokens + completion_tokens + side_tokens
    total_cost = cost + side_cost
    reason = (response.choices[0].message.content or "").strip()
    log(f"[workflow] step=6 reason_call tokens={prompt_tokens + completion_tokens} cost={cost:.6f} reason={reason!r}")

    # Step 7: return the final claim decision, fixed contract, always last.
    output = make_output(claim_id, status, covered, exclusion, gross_amount, excess, payable_amount, reason)

    wall_ms = int((time.monotonic() - wall_start) * 1000)
    return {
        "claim_id": claim_id,
        "output": output,
        "termination_reason": "completed",
        "iterations": 1,
        "tokens_total": tokens_total,
        "cost_total": total_cost,
        "wall_clock_ms": wall_ms,
        "log": log_lines,
    }
