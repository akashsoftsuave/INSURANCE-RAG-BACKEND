"""Output guard: the system triages and cites; the adjuster decides.

`covered` (and its siblings: approved / declined / payable ...) must never appear in the
assistant's OWN voice. Enforced here in code on every response; the eval suite asserts it on
the final output, independent of what the prompt asked for.

Quoted policy wording is carried in separate `excerpt` fields and is exempt: the policy text
itself says "covered" in places, and hiding the wording from the adjuster would be worse.
"""
from __future__ import annotations

import re

from capstone.config import FORBIDDEN_V1, FORBIDDEN_VERDICT_PATTERNS

_RX = [re.compile(p, re.I) for p in FORBIDDEN_VERDICT_PATTERNS]

TEMPLATE_SUMMARY = ("Referred to an adjuster. The wording in force on the loss date, the policy-period check and the "
                    "three financial figures are listed below for the adjuster's decision.")


_RX_V1 = [re.compile(p, re.I) for p in FORBIDDEN_V1]
_DASHES = dict.fromkeys(range(0x2010, 0x2016), "-")   # the model emits U+2011 in dates and ids


def normalize(text: str | None) -> str | None:
    return text.translate(_DASHES) if text else text


def violations(text: str | None, version: str = "v2") -> list[str]:
    if not text:
        return []
    rxs = _RX_V1 if version == "v1" else _RX
    return sorted({m.group(0).lower() for rx in rxs for m in rx.finditer(text)})


def enforce_summary(text: str | None) -> tuple[str, dict]:
    """Return (safe_text, info). Never returns text containing a forbidden phrase."""
    text = normalize(text)
    bad = violations(text)
    if not text or bad:
        return TEMPLATE_SUMMARY, {"guard_replaced": True, "violations": bad}
    return text, {"guard_replaced": False, "violations": []}
