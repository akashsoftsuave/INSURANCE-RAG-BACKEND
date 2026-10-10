"""Deterministic indexes written onto every request-log line.

They exist so the log can be SLICED, not read. The drill complaint is about
the output ("it told a claimant something was covered"), so an index on the
input alone (input_type) is not enough: the coverage verdict the answer gave
has to be an indexed field too, along with which retrieved chunks carried
exclusion language. No LLM is involved — an index must be cheap, stable and
identical on replay.
"""

from __future__ import annotations

import re

# ---- input side -----------------------------------------------------------

_INPUT_RULES = [
    ("coverage_question", re.compile(
        r"\b(cover(?:ed|s|age)?|excluded?|exclusion|pay(?:s)? (?:for|out|if)|"
        r"claim (?:for|if)|will (?:the|my) (?:policy|insurer) pay|reimburs\w*)\b", re.I)),
    ("claim_status", re.compile(r"\b(status|approved|rejected|under review|settled)\b|\bCLM\b", re.I)),
    ("amount_limit", re.compile(
        r"\b(how much|limit|deductible|excess|premium|sum insured|co-?pay|amount|cost|price)\b", re.I)),
    ("claim_process", re.compile(r"\b(how (?:do|can) i (?:claim|file)|documents?|steps?|appeal|grievance)\b", re.I)),
    ("policy_details", re.compile(
        r"\b(expir\w*|start|policy number|nominee|beneficiar\w*|insurer|registration|plan|hospital)\b", re.I)),
]


def classify_input_type(question: str) -> str:
    for label, rx in _INPUT_RULES:
        if rx.search(question or ""):
            return label
    return "other"


# ---- output side ----------------------------------------------------------

_NEGATIVE = re.compile(
    r"\b(not covered|isn['’]t covered|aren['’]t covered|is excluded|are excluded|excluded|"
    r"does not cover|doesn['’]t cover|not (?:be )?paid|will not pay|won['’]t pay|no coverage|"
    r"not included|listed under [“\"']?what['’]s not covered)\b", re.I)
_POSITIVE = re.compile(
    r"\b(is covered|are covered|covers|will (?:pay|cover)|includes? (?:\w+ ){0,3}cover(?:age)?|"
    r"provides? (?:\w+ ){0,3}cover(?:age)?|eligible)\b", re.I)
_NOT_STATED = re.compile(r"\b(does not (?:say|state|mention|specify)|not (?:stated|mentioned|specified)|"
                         r"no information)\b", re.I)


def _lead(answer: str) -> str:
    text = re.sub(r"[*_#>`]", "", answer or "").strip()
    return text[:220]


def classify_coverage_verdict(answer: str, input_type: str | None = None) -> str:
    """covered / not_covered / partial / not_stated / n/a.

    Reads the lead of the answer first (an explicit "Yes"/"No" opening wins),
    then the whole answer, negative patterns before positive ones so
    "not covered" is never read as "covered"."""
    if input_type is not None and input_type != "coverage_question":
        return "n/a"
    lead = _lead(answer)
    low = lead.lower()
    if re.match(r"^not (stated|mentioned|specified)\b", low):
        return "not_stated"
    if re.match(r"^(no|not)\b", low):
        return "not_covered"
    if re.match(r"^(partially|partly)\b", low):
        return "partial"
    if re.match(r"^yes\b", low):
        # "Yes, but only ..." with a negative in the lead is a partial answer.
        return "partial" if _NEGATIVE.search(lead) else "covered"
    if _NEGATIVE.search(lead):
        return "not_covered"
    if _POSITIVE.search(lead):
        return "covered"
    full = answer or ""
    if _NEGATIVE.search(full):
        return "not_covered"
    if _POSITIVE.search(full):
        return "covered"
    if _NOT_STATED.search(full):
        return "not_stated"
    return "not_stated"


# ---- context side ---------------------------------------------------------

EXCLUSION_MARKERS = re.compile(
    r"what['’]s not covered|not covered|\bexclusions?\b|\bexcluded\b|"
    r"claim might get rejected|not indemnified", re.I)


def exclusion_context_ids(chunks: list[dict]) -> list[str]:
    """Ids of the retrieved chunks that contain exclusion language. With this
    on the log line, "did retrieval miss the exclusions schedule, or did the
    model ignore it?" is a one-hop question."""
    return [str(c.get("id")) for c in chunks if EXCLUSION_MARKERS.search(c.get("document", "") or "")]
