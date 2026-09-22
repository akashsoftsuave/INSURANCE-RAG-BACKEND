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
