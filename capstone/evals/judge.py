"""LLM judge on the INTEGRATED outputs. Assertions first (assertions.py); the judge only grades what code cannot:
is the model-written summary faithful to the clauses/figures it was shown, and does it keep the three numbers apart.

Re-uses the Week-6 judge prompt (eval/judge_v2.txt, QUESTION/CONTEXT/ANSWER, PASS/FAIL + mode) and its parser.
v2 is the prompt that was validated at >= 85% on the Week-6 traces; `capstone/evals/judge_integrated_v3.txt` is the
version adapted after re-checking it on integrated outputs (see reports/judge_agreement.md for the numbers).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from groq import Groq, RateLimitError

from app.core.config import settings
from eval.judge_runner import parse_verdict

PROMPTS = {"v2": Path("eval/judge_v2.txt"), "v3": Path("capstone/evals/judge_integrated_v3.txt")}
_WAIT = re.compile(r"try again in (?:(\d+)m)?([\d.]+)s", re.I)


def build_message(case: dict, run: dict) -> str:
    ctx = [f"{c['citation']}\n{c['excerpt']}" for c in run["citations"]]
    ctx.append("POLICY PERIOD CHECK: " + json.dumps(run.get("in_force")))
    ctx.append("FIGURES FROM LIMITS TABLE (INR): " + json.dumps(run.get("limits")))
    q = ("Write a neutral triage summary for a human adjuster for this FNOL: "
         f"\"{run.get('fnol_redacted')}\". It must report the loss date, whether the policy period includes it, which "
         "endorsement version was applied, and the deductible, sub-limit and policy limit as three separate figures. "
         "It must not state or imply a coverage decision.")
    return f"QUESTION:\n{q}\n\nCONTEXT:\n" + "\n\n".join(ctx) + f"\n\nANSWER:\n{run['summary']}"


def judge_run(client: Groq, system_prompt: str, case: dict, run: dict) -> dict:
    msg = build_message(case, run)
    for attempt in range(8):
        try:
            r = client.chat.completions.create(model=settings.MODEL_NAME, temperature=0, max_tokens=500,
                                               messages=[{"role": "system", "content": system_prompt},
                                                         {"role": "user", "content": msg}])
            v = parse_verdict(r.choices[0].message.content)
            v["tokens"] = r.usage.total_tokens
            return v
        except RateLimitError as e:
            m = _WAIT.search(str(e))
            time.sleep((int(m.group(1) or 0) * 60 + float(m.group(2)) + 1) if m else 20)
    return {"verdict": "PARSE_ERROR", "mode": "OTHER", "reason": "rate limit retries exhausted"}


def judge_all(items: list[tuple[dict, dict]], version: str = "v2") -> dict[str, dict]:
    client = Groq(api_key=settings.GROQ_API_KEY, max_retries=0)
    prompt = PROMPTS[version].read_text(encoding="utf-8")
    return {case["case_id"]: judge_run(client, prompt, case, run) for case, run in items}
