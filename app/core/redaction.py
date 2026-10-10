"""Redaction applied to trace records before they are written to disk.

This runs on the trace-bound copy of text only — the live answer returned to
the API caller is never redacted.

ID pattern (policy numbers, claim numbers): regex on the
"LETTERS-LETTERS-YYYY-ALPHANUM" shape used throughout insurance policy
documents (e.g. "TEST-INS-2026-001847", "CLM-2026-00421").

The hyphen class below also matches the typographic hyphen/dash variants
(U+2010-2014) the LLM substitutes for ASCII "-" in markdown-formatted
answers (e.g. "CLM\u20112026\u201100437") — an ASCII-only "-" here let claim
numbers leak into traces/traces.jsonl unredacted (found via Task Set D's
claim-number check on the 30-trace baseline; see eval/regression_runner.py).
"""

import re

_HYPHENS = "\\-\u2010\u2011\u2012\u2013\u2014"
_ID_PATTERN = re.compile(rf"\b(?:[A-Z]{{2,10}}[{_HYPHENS}]){{1,3}}\d{{4}}[{_HYPHENS}][A-Z0-9]+\b")


def redact(text: str | None) -> str | None:
    if not text:
        return text

    return _ID_PATTERN.sub("[ID_REDACTED]", text)


# ---------------------------------------------------------------------------
# Week 11 — redact-on-write for the request log.
#
# The request log is searchable by design (that is the point of the drill),
# which is exactly why claimant PII must never reach it: adjusters paste
# claimant names, phone numbers, emails and vehicle numbers into questions.
# redact_pii() is applied to every free-text field BEFORE the line is
# written — there is no "clean it later" pass. It is a superset of redact():
# the same policy/claim id rule, plus the PII shapes seen in this corpus
# (ACKO bike schedule: owner name, Indian mobile, email, TN registration,
# DBTR policy number, pincode).
# ---------------------------------------------------------------------------

_PII_RULES = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[EMAIL]"),
    (re.compile(r"(?:\+91[\s-]?)?\b[6-9]\d{4}[\s-]?\d{5}\b"), "[PHONE]"),
    (re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"), "[GOVT_ID]"),
    (re.compile(r"\b[A-Z]{2}\s?\d{1,2}\s?[A-Z]{1,3}\s?\d{4}\b"), "[VEHICLE_REG]"),
    (re.compile(r"\b[A-Z]{3,5}\d{8,}(?:/\d+)?\b"), "[POLICY_NO]"),
    (re.compile(r"\b(?:pin\s*code|pincode)\s*:?\s*\d{6}\b", re.I), "[PINCODE]"),
    # A name is only redacted where the text marks it as a person, since a
    # bare capitalised word is usually a policy term ("Theft", "Fire").
    # Keyword is case-insensitive, the name itself must be Capitalised.
    (re.compile(r"\b((?i:claimant|insured|customer|policyholder|owner|nominee|"
                r"mr|mrs|ms|dr)\.?\s+)[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]*\.?){0,2}"),
     r"\1[NAME]"),
]


def redact_pii(text: str | None) -> str | None:
    if not text:
        return text
    out = redact(text)
    for pattern, repl in _PII_RULES:
        out = pattern.sub(repl, out)
    return out
