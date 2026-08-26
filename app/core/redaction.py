"""Redaction applied to trace records before they are written to disk.

This runs on the trace-bound copy of text only — the live answer returned to
the API caller is never redacted.

ID pattern (policy numbers, claim numbers): regex on the
"LETTERS-LETTERS-YYYY-ALPHANUM" shape used throughout insurance policy
documents (e.g. "TEST-INS-2026-001847", "CLM-2026-00421").
"""

import re

_ID_PATTERN = re.compile(r"\b(?:[A-Z]{2,10}-){1,3}\d{4}-[A-Z0-9]+\b")


def redact(text: str | None) -> str | None:
    if not text:
        return text

    return _ID_PATTERN.sub("[ID_REDACTED]", text)
