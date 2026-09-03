"""
Task Set D — deterministic (non-LLM) checks for the insurance eval harness.

These four checks are objective on purpose: whenever an answer states one
of these fields, its *format* can be verified by code with zero ambiguity,
so it must never be delegated to the LLM judge (see eval/judge_v1.txt,
which explicitly refuses to grade them — that's requirement "move these
objective checks out of the LLM judge").

Each check returns NOT_APPLICABLE when its field isn't mentioned in the
text at all. That is a legitimate, expected outcome, not a failure to be
forced — see eval/regression_runner.py's output against the real 30-trace
population (traces/traces.jsonl, 3-document set): those are policy-info
Q&A traces, not claims-decision/FNOL traces, so "date of loss" and
"denial" never occur in an answer, and both checks report
NOT_APPLICABLE on all 30 by design. The claim-number and deductible
checks DO fire on real data (source ground truth: eval/data/*.pdf via
eval/questions/*.json markers, e.g. "CLM-2026-00421", "10,000" deductible).
"""

import re
from datetime import datetime

PASS = "PASS"
FAIL = "FAIL"
NOT_APPLICABLE = "NOT_APPLICABLE"

# Same hyphen class as app/core/redaction.py: the LLM's markdown output
# substitutes typographic hyphens/dashes (U+2010-2014) for ASCII "-".
_HYPHENS = "\\-\u2010\u2011\u2012\u2013\u2014"

_CLAIM_TOKEN = re.compile(rf"\bCLM\s*[{_HYPHENS}]?\s*\d+\s*[{_HYPHENS}]?\s*\d+\b", re.IGNORECASE)
_CLAIM_STRICT = re.compile(rf"^CLM[{_HYPHENS}]\d{{4}}[{_HYPHENS}]\d{{5}}$", re.IGNORECASE)

_DATE_LABEL = re.compile(
    r"(?:date of loss|loss date|incident date|claim date)\s*[:\-]?\s*([^,.;\n]{1,30})",
    re.IGNORECASE,
)
_DATE_FORMATS = ("%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %m %Y")

_DEDUCTIBLE_LABEL = re.compile(r"(deductible|excess)\b", re.IGNORECASE)
_EXPLICIT_ABSENCE = re.compile(
    r"\b(?:no|not\s+list(?:ed)?(?:\s+a)?\s+separate|none)\b.{0,40}"
    r"(?:deductible|excess)|(?:deductible|excess).{0,40}\b(?:none|no\s+deductible)\b",
    re.IGNORECASE,
)
_DENIAL_WORD = re.compile(r"\b(?:reject|denied|declin)\w*\b", re.IGNORECASE)
_EXCLUSION_REF = re.compile(r"\b(?:exclusion|excluded|clause|section\s*\d+)\b", re.IGNORECASE)


def check_claim_number_format(text: str) -> dict:
    """Claim number, when present, must match CLM-YYYY-NNNNN (5-digit
    sequence number), tolerant of the LLM's typographic-hyphen markdown
    formatting."""
    tokens = _CLAIM_TOKEN.findall(text or "")
    if not tokens:
        return {"check": "claim_number_format", "status": NOT_APPLICABLE,
                "detail": "no claim-number-shaped token in text"}
    normalized = [re.sub(r"\s+", "", t) for t in tokens]
    bad = [t for t in normalized if not _CLAIM_STRICT.match(t)]
    if bad:
        return {"check": "claim_number_format", "status": FAIL,
                "detail": f"malformed claim number(s), expected CLM-YYYY-NNNNN: {bad}"}
    return {"check": "claim_number_format", "status": PASS,
            "detail": f"{len(normalized)} claim number(s) all match CLM-YYYY-NNNNN: {normalized}"}


def check_date_of_loss(text: str) -> dict:
    """A 'date of loss' (or claim/loss/incident date) field, when stated,
    must be a parseable calendar date."""
    m = _DATE_LABEL.search(text or "")
    if not m:
        return {"check": "date_of_loss", "status": NOT_APPLICABLE,
                "detail": "no date-of-loss/claim-date field in text"}
    raw = m.group(1).strip()
    for fmt in _DATE_FORMATS:
        try:
            datetime.strptime(raw, fmt)
            return {"check": "date_of_loss", "status": PASS,
                    "detail": f"'{raw}' parses as a date ({fmt})"}
        except ValueError:
            continue
    return {"check": "date_of_loss", "status": FAIL,
            "detail": f"'{raw}' found next to a date-of-loss label but does not parse"}


def check_deductible_numeric(text: str) -> dict:
    """A stated deductible/excess must be a numeric amount, unless the
    text explicitly states there isn't one (a valid semantic value, not
    a parse failure)."""
    if not _DEDUCTIBLE_LABEL.search(text or ""):
        return {"check": "deductible_numeric", "status": NOT_APPLICABLE,
                "detail": "no deductible/excess mention in text"}
    if _EXPLICIT_ABSENCE.search(text):
        return {"check": "deductible_numeric", "status": PASS,
                "detail": "deductible/excess explicitly stated as absent (valid, not a numeric-parse failure)"}
    # Sentence-splitting on ". " is unreliable here — currency abbreviations
    # like "Rs." end in a period, so "deductible is **Rs.\n10,000**." was
    # once wrongly split right between "Rs." and "10,000", separating the
    # label from its own number. Use a plain character window around each
    # occurrence instead, and require only ONE occurrence to be supported —
    # a later back-reference ("...the deductible is not the same as a
    # co-pay") legitimately doesn't restate the number.
    supported = False
    occurrences = 0
    for m in _DEDUCTIBLE_LABEL.finditer(text):
        occurrences += 1
        window = text[max(0, m.start() - 80):m.start() + 80]
        if any(c.isdigit() for c in window):
            supported = True
            break
    if supported:
        return {"check": "deductible_numeric", "status": PASS,
                "detail": f"numeric value found near at least one of {occurrences} "
                          "deductible/excess mention(s)"}
    return {"check": "deductible_numeric", "status": FAIL,
            "detail": f"deductible/excess mentioned {occurrences} time(s), none with a nearby "
                      "numeric value and no explicit absence statement anywhere in the text"}


def check_denial_requires_exclusion(text: str) -> dict:
    """A denial/rejection statement must cite the exclusion it falls
    under (by name, section, or clause) — an unexplained denial is a
    FAIL. No numbered exclusion sub-clauses exist anywhere in the
    3-document eval corpus (eval/data/*.pdf, section 7/5 'Exclusions' is
    an unnumbered bullet list), so a textual exclusion/section reference
    is accepted as the finest granularity actually available in the
    source data — see docstring above."""
    if not _DENIAL_WORD.search(text or ""):
        return {"check": "denial_requires_exclusion", "status": NOT_APPLICABLE,
                "detail": "no denial/rejection language in text"}
    if _EXCLUSION_REF.search(text):
        return {"check": "denial_requires_exclusion", "status": PASS,
                "detail": "denial is accompanied by an exclusion/clause/section reference"}
    return {"check": "denial_requires_exclusion", "status": FAIL,
            "detail": "denial stated with no exclusion/clause reference"}


def run_all_checks(text: str) -> list:
    return [
        check_claim_number_format(text),
        check_date_of_loss(text),
        check_deductible_numeric(text),
        check_denial_requires_exclusion(text),
    ]
