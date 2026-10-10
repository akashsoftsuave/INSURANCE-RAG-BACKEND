import hashlib
import time

from groq import Groq

from app.core.config import settings

# Bump this whenever the prompt template below changes — trace records store
# it so a replayed trace can be told apart from one built on an older prompt.
# Week 11: this is the version legacy callers (replay_trace.py, run_eval.py,
# the agentic flow) get when they pass no prompt_version. Live chat traffic is
# routed through select_prompt_version() instead, which applies the canary.
PROMPT_VERSION = "insurance-qa-v2"
TEMPERATURE = 0
SYSTEM_MESSAGE = "You answer only using provided documents."


def _template_v2(question: str, context: str) -> str:
    return f"""You are an Insurance Assistant.

Rules:

1. Answer the user's question using all relevant information in the provided context.

2. The question may contain multiple requested fields.
   Check each requested field independently.

3. Check ALL provided context chunks before deciding that information is unavailable.

4. Do not say information is unavailable unless you have verified that the provided context contains no information for that specific field.

5. If some fields are available and others are unavailable:
   - answer the available fields
   - explicitly identify only the unavailable fields.

6. Do not use outside knowledge.

7. Do not infer or invent information that is not explicitly stated in the context.

8. If relevant information is spread across multiple context chunks, combine it into one answer.

9. Preserve the exact names, dates, amounts, percentages, limits, and policy terms stated in the context.

Context:

{context}

Question:

{question}
"""


def _context_v2(chunks: list[dict]) -> str:
    # v2 behaviour, unchanged: chunks in rerank order, no labels.
    return "\n\n".join(c["document"] for c in chunks)


# Prompt registry. A version is the PAIR (context assembly, template): both
# decide what the model reads, so changing either one is a version bump.
PROMPTS = {
    "insurance-qa-v2": {"template": _template_v2, "context": _context_v2},
}


# ---------------------------------------------------------------------------
# insurance-qa-v3 (Week 11 drill fix). Two changes, both inside the prompt
# layer, versioned together:
#
# 1. Template: an explicit coverage-vs-exclusion rule. The found failure had
#    the "What's not covered" chunk IN its context (chunk 2) and still said
#    theft was covered: on the ACKO schedule every excluded item is worded
#    like a benefit ("Coverage for losses ... in the event of theft"), and v2
#    has no rule saying an exclusion heading flips that. v3 says it, and makes
#    the answer open with a fixed Yes/No/Partially/Not stated token so the
#    verdict is unambiguous to the adjuster and to the log's output index.
# 2. Context: chunks are labelled and put back in DOCUMENT order instead of
#    rerank order. The chunker split that list across chunks 2/3/4, so in
#    rerank order "Rat Bite"/"Fire" can arrive above their own heading; in
#    document order the heading is read first and the list reads as a list.
#    The SET of chunks (the context ids) is unchanged — retrieval is untouched.
# ---------------------------------------------------------------------------

_V3_COVERAGE_RULES = """
10. Coverage questions ("is X covered?", "will the policy pay for X?"):
   a. Before answering, check every context chunk for exclusion language:
      headings such as "What's not covered", "Exclusions", "What are the
      reasons my claim might get rejected?", and phrases such as "not
      covered", "excluded", "not indemnified".
   b. Every item listed under a "What's not covered" (or "Exclusions")
      heading is NOT covered by this policy, even when the item's own
      description is worded like a benefit (for example "Theft - Coverage for
      losses in the event of theft"). That wording describes the cover this
      plan does NOT provide.
   c. Context chunks are given in document order. A list continues across
      chunk boundaries: items that follow an exclusion heading belong to that
      exclusion list until a new heading starts.
   d. An exclusion always overrides a general statement of cover.
   e. Start the answer with exactly one of "Yes -", "No -", "Partially -" or
      "Not stated -", then name the heading or clause that decides it and its
      page.
"""


def _template_v3(question: str, context: str) -> str:
    base = _template_v2(question, context)
    marker = "\nContext:\n"
    head, tail = base.split(marker, 1)
    return head.rstrip() + "\n" + _V3_COVERAGE_RULES + "\n" + marker + tail


def _doc_order_key(chunk: dict):
    cid = str(chunk.get("id", ""))
    page = (chunk.get("metadata") or {}).get("page") or 0
    return (int(cid), 0) if cid.isdigit() else (10**9 + int(page), cid)


def _context_v3(chunks: list[dict]) -> str:
    parts = []
    for c in sorted(chunks, key=_doc_order_key):
        meta = c.get("metadata") or {}
        section = meta.get("section")
        label = f"[chunk {c.get('id')} | page {meta.get('page')}"
        if section and section != "Unknown":
            label += f" | section {' '.join(str(section).split())}"
        parts.append(label + "]\n" + c["document"])
    return "\n\n".join(parts)


# REJECTED CANDIDATE — kept registered so its suite run stays reproducible,
# never shipped. Suite: 27/32. Verdicts were fixed, but rule (e) made the
# model answer with ONLY the token and heading ("No - Exclusions (page 5)"):
# the item asked about was never named and the Week-6 cosmetic case lost the
# word "excluded" (2 Week-6 regressions + 3 must_mention failures).
PROMPTS["insurance-qa-v3"] = {"template": _template_v3, "context": _context_v3}


# insurance-qa-v4 — the shipped fix. Identical to v3 (same rules a-d, same
# document-order labelled context) except rule (e), which now demands the
# full explanation after the verdict token instead of only a heading.
_V4_COVERAGE_RULES = _V3_COVERAGE_RULES.replace(
    """   e. Start the answer with exactly one of "Yes -", "No -", "Partially -" or
      "Not stated -", then name the heading or clause that decides it and its
      page.
""",
    """   e. Start the answer with exactly one of "Yes -", "No -", "Partially -" or
      "Not stated -". Then, in one to three full sentences: name the item
      asked about, say plainly whether it is covered or excluded (use the
      word "excluded" or "not covered" when it is not covered), quote the
      deciding wording from the context, and name its heading and page. Add
      any limit, deductible or co-pay that applies.
""")
assert _V4_COVERAGE_RULES != _V3_COVERAGE_RULES


def _template_v4(question: str, context: str) -> str:
    base = _template_v2(question, context)
    marker = "\nContext:\n"
    head, tail = base.split(marker, 1)
    return head.rstrip() + "\n" + _V4_COVERAGE_RULES + "\n" + marker + tail


PROMPTS["insurance-qa-v4"] = {"template": _template_v4, "context": _context_v3}


def select_prompt_version(user_id: str | None) -> str:
    """Canary routing. A stable hash of user_id picks a bucket 0-99; buckets
    below PROMPT_CANARY_PCT get the canary version. The same adjuster always
    lands in the same bucket, so one person never flips between versions
    mid-conversation, and the request log can be sliced by prompt_version to
    compare the two populations."""
    canary = settings.PROMPT_CANARY_VERSION
    pct = max(0, min(100, settings.PROMPT_CANARY_PCT))
    if canary and canary in PROMPTS and pct > 0:
        bucket = int(hashlib.sha256((user_id or "anonymous").encode()).hexdigest(), 16) % 100
        if bucket < pct:
            return canary
    return settings.PROMPT_STABLE_VERSION


class LLMService:

    def __init__(self):

        print(f"[LLM][Init] Groq client | model={settings.MODEL_NAME}")
        self.client = Groq(
            api_key=settings.GROQ_API_KEY
        )

    @staticmethod
    def build_prompt(question: str, context: str, prompt_version: str | None = None) -> str:
        """Pure template render — used both for live calls and for replaying
        a trace from its stored (question, context, prompt_version)."""
        version = prompt_version or PROMPT_VERSION
        return PROMPTS[version]["template"](question, context)

    @staticmethod
    def build_context(chunks: list[dict], prompt_version: str | None = None) -> str:
        version = prompt_version or PROMPT_VERSION
        return PROMPTS[version]["context"](chunks)

    def generate(self, question: str, context: str, prompt_version: str | None = None) -> dict:
        """Returns the raw output plus everything a trace needs to reproduce
        this call later: prompt_version, model name, temperature.

        Week 11: also returns the provider's own timing split (queue / prompt
        / completion seconds) and the rate-limit headers of this response, so
        the generation span can show where its latency went and how much
        headroom was left — the input to the 10x capacity question."""

        version = prompt_version or PROMPT_VERSION
        if version not in PROMPTS:
            raise ValueError(f"unknown prompt_version {version!r}; known: {sorted(PROMPTS)}")
        prompt = self.build_prompt(question, context, version)

        print(f"[LLM] Requesting answer | model={settings.MODEL_NAME} prompt_version={version} "
              f"context_chars={len(context)} prompt_chars={len(prompt)}")

        started = time.monotonic()
        raw = self.client.chat.completions.with_raw_response.create(
            model=settings.MODEL_NAME,
            temperature=TEMPERATURE,
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_MESSAGE
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )
        wall_ms = int((time.monotonic() - started) * 1000)
        response = raw.parse()

        # Token usage is reported so callers can account for cost/latency per
        # answer. Purely additive — nothing about the generation itself changes.
        usage = getattr(response, "usage", None)
        headers = raw.headers

        def _hdr(name: str):
            value = headers.get(name)
            try:
                return int(value) if value is not None else None
            except ValueError:
                return value

        return {
            "raw_output": response.choices[0].message.content,
            "prompt_version": version,
            "model": settings.MODEL_NAME,
            "temperature": TEMPERATURE,
            "usage": {
                "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            },
            "provider_timing_ms": {
                "wall": wall_ms,
                "queue": round((getattr(usage, "queue_time", 0) or 0) * 1000, 1),
                "prompt": round((getattr(usage, "prompt_time", 0) or 0) * 1000, 1),
                "completion": round((getattr(usage, "completion_time", 0) or 0) * 1000, 1),
            },
            "rate_limit": {
                "limit_requests_per_day": _hdr("x-ratelimit-limit-requests"),
                "remaining_requests_today": _hdr("x-ratelimit-remaining-requests"),
                "limit_tokens_per_minute": _hdr("x-ratelimit-limit-tokens"),
                "remaining_tokens_this_minute": _hdr("x-ratelimit-remaining-tokens"),
            },
        }
