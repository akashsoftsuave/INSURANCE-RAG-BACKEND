"""Centralized token pricing, so every cost number in the race is computed
from one table instead of being hand-calculated in multiple places.

Rates are USD per 1M tokens, Groq standard (non-batch) pricing for the
model this project already uses (app.core.config.settings.MODEL_NAME).

Source: Groq's published rate for openai/gpt-oss-120b, $0.15 / 1M input
tokens and $0.60 / 1M output tokens, checked 2026-09-07. If MODEL_NAME is
changed in .env to a model not in this table, DEFAULT_RATE is used and a
warning is printed once — cost numbers for an unpriced model should not be
trusted without updating this table.
"""

from __future__ import annotations

MODEL_PRICING = {
    "openai/gpt-oss-120b": {"input_per_m": 0.15, "output_per_m": 0.60},
}

DEFAULT_RATE = {"input_per_m": 0.15, "output_per_m": 0.60}

_warned_models: set[str] = set()


def get_rate(model: str) -> dict:
    rate = MODEL_PRICING.get(model)
    if rate is None:
        if model not in _warned_models:
            print(f"[pricing] WARNING: no rate table entry for model={model!r}; "
                  f"falling back to default rate {DEFAULT_RATE}. Update "
                  f"app/claims_agent/pricing.py if this model is used for real.")
            _warned_models.add(model)
        rate = DEFAULT_RATE
    return rate


def call_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rate = get_rate(model)
    return (prompt_tokens / 1_000_000) * rate["input_per_m"] + \
           (completion_tokens / 1_000_000) * rate["output_per_m"]
