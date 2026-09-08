from __future__ import annotations

from app.claims_agent.pricing import call_cost

TRIGGER_TURNS = 12
KEEP_RECENT_TURNS = 6

SUMMARY_PROMPT = """Summarize the following portion of an insurance claim's
adjuster-notes conversation log in 2-3 sentences. Focus on the overall
narrative of what happened with the claim so far.

LOG:
{log}
"""


def split_turns(notes: str) -> list[str]:
    return [line.strip() for line in (notes or "").split("\n") if line.strip()]


def apply_sliding_window(notes: str, client, model: str) -> tuple[str, dict | None]:
    """Returns (windowed_text, usage). usage is None if no summarization
    was needed (notes were short enough already)."""
    turns = split_turns(notes)
    if len(turns) <= TRIGGER_TURNS:
        return notes, None

    older = turns[:-KEEP_RECENT_TURNS]
    recent = turns[-KEEP_RECENT_TURNS:]

    prompt = SUMMARY_PROMPT.format(log="\n".join(older))
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    usage = response.usage
    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
    cost = call_cost(model, prompt_tokens, completion_tokens)
    summary_text = (response.choices[0].message.content or "").strip()

    windowed = (
        f"[SUMMARY OF {len(older)} EARLIER LOG ENTRIES]: {summary_text}\n"
        f"[LAST {len(recent)} LOG ENTRIES, VERBATIM]:\n" + "\n".join(recent)
    )
    return windowed, {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "tokens": prompt_tokens + completion_tokens,
        "cost": cost,
        "original_turns": len(turns),
        "summarized_turns": len(older),
        "kept_recent_turns": len(recent),
    }
