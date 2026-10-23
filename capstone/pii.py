"""The PII line. Claimant names, addresses, phone numbers and injuries must not reach
(a) any log line, (b) anything the MCP server returns, (c) the model's context via lookup tools.

Two layers, because regexes alone cannot find a bare name:
  1. structured: the claim record knows its own PII fields, so those exact strings are scrubbed
     (this is what covers names/addresses in free-text notes);
  2. pattern: app.core.redaction.redact_pii (phones, emails, vehicle regs, ids) + an injury lexicon
     + an address-shape pattern.

KNOWN LIMIT: a name that is NOT in the structured record (e.g. a third party named inside free text)
is only caught if a marker word precedes it ("claimant", "Mr", ...). Listed in WEAKNESSES.md.
"""
from __future__ import annotations

import re

from app.core.redaction import _PII_RULES, redact_pii

_INJURY = re.compile(
    r"\b(?:fractur\w*|whiplash|lacerat\w*|concussion|bruis\w*|sprain\w*|burns?|"
    r"strain\w*|dislocat\w*)\b(?:\s+(?:and|on|of|to)\s+(?:the\s+)?[\w ]{1,25})?", re.I)
_ADDR = re.compile(r"\b\d{1,3},\s+[A-Z][\w ]+?,\s+[A-Z][a-z]+(?:\s+\d{6})?")


def scrub(text: str | None, pii: dict | None = None, keep_ids: bool = False) -> str | None:
    """keep_ids=True keeps claim/policy numbers (internal reference ids the adjuster and the replay need)
    while still removing every personal field. Free-text logs use keep_ids=False."""
    if not text:
        return text
    out = text
    for key, tag in (("address", "[ADDRESS]"), ("injury", "[INJURY]"), ("phone", "[PHONE]"), ("name", "[NAME]")):
        val = (pii or {}).get(key)
        if val:
            out = out.replace(val, tag)
            if key == "name":  # surname-only / first-name-only echoes
                for part in val.split():
                    out = re.sub(rf"\b{re.escape(part)}\b", tag, out)
    out = _ADDR.sub("[ADDRESS]", out)
    out = _INJURY.sub("[INJURY]", out)
    if keep_ids:
        for pattern, repl in _PII_RULES:
            out = pattern.sub(repl, out)
        return out
    return redact_pii(out)


def pii_strings(pii: dict | None) -> list[str]:
    """Canary strings used by eval assertions to prove a leak did/didn't happen."""
    if not pii:
        return []
    out = [v for v in (pii.get("name"), pii.get("address"), pii.get("phone"), pii.get("injury")) if v]
    if pii.get("name"):
        out += pii["name"].split()
    return out


def leaked(blob: str, pii: dict | None) -> list[str]:
    return [s for s in pii_strings(pii)
            if len(s) >= 3 and re.search(rf"(?<!\w){re.escape(s)}(?!\w)", blob, re.I)]
