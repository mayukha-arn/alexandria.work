"""Layer 7: runtime guardrails.

* ``check_input``  - direct prompt-injection screen on user questions.
* ``scan_text``    - indirect-injection scan for document text (run at index time and at
                     retrieval, because documents are untrusted input to the LLM).
* ``mask_pii``     - DLP on model output: SSNs, Luhn-valid card numbers, API keys / tokens.

Regex screening is a speed bump, not a wall. The real controls are structural: retrieval
is filtered by clearance in the database, retrieved text is passed to the model as quoted
data, and the model's output is masked before it reaches the client.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import List

MAX_QUESTION_CHARS = 2000

# Zero-width / bidi control characters are used to hide instructions from human reviewers
# and to split trigger phrases so simple regexes miss them.
_INVISIBLE = re.compile("[​‌‍⁠﻿‪-‮⁦-⁩]")

_INJECTION = [re.compile(p, re.IGNORECASE) for p in (
    r"\b(ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|any|your)\b[^.\n]{0,40}\b(instructions?|prompts?|rules?|guidelines?|directives?|restrictions?)\b",
    r"\b(reveal|show|print|repeat|output|leak|display)\b[^.\n]{0,40}\b(system|hidden|initial|original|secret)\b[^.\n]{0,20}\b(prompt|instructions?|message)\b",
    r"\bsystem\s*(prompt|message)?\s*:\s*(override|ignore|you)",
    r"\byou\s+are\s+now\b",
    r"\bact\s+as\b[^.\n]{0,30}\b(admin|administrator|root|system|developer\s+mode|unrestricted|jailbroken)\b",
    r"\b(developer|god|dan|jailbreak)\s+mode\b",
    r"\bdo\s+anything\s+now\b",
    r"\bpretend\b[^.\n]{0,40}\b(no\s+(rules|restrictions|limits)|unrestricted|clearance)\b",
    r"\b(my|the)\s+(clearance|role|privilege)s?\s+(is|are|level)\b[^.\n]{0,30}\b(admin|100|senior|root|elevated)\b",
    r"\b(output|print|dump|list)\b[^.\n]{0,30}\b(all|every)\b[^.\n]{0,30}\b(users?|hash(es)?|keys?|secrets?|documents?|credentials?)\b",
    r"<\s*/?\s*(system|assistant|instructions?)\s*>",
    r"\[\s*/?\s*INST\s*\]",
)]


def normalize(text: str) -> str:
    """Fold look-alike characters, drop invisible ones, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Verdict:
    blocked: bool
    reasons: List[str] = field(default_factory=list)


def scan_text(text: str) -> List[str]:
    """Findings (human-readable) for injection attempts or hidden text in ``text``."""
    findings: List[str] = []
    if _INVISIBLE.search(text):
        findings.append("hidden zero-width / bidi control characters")
    norm = normalize(text)
    for pat in _INJECTION:
        m = pat.search(norm)
        if m:
            findings.append(f"injection phrase: {m.group(0)[:80]!r}")
    return findings


def check_input(question: str) -> Verdict:
    if not question or not question.strip():
        return Verdict(True, ["empty question"])
    if len(question) > MAX_QUESTION_CHARS:
        return Verdict(True, [f"question longer than {MAX_QUESTION_CHARS} characters"])
    findings = scan_text(question)
    return Verdict(bool(findings), findings)


# ------------------------------------------------------------------------------- DLP

_SSN = re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_KEYS = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|AIza[0-9A-Za-z_-]{30,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b"
    r"|(?i:bearer)\s+[A-Za-z0-9\-._~+/]{20,}=*"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
)


def luhn_valid(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def mask_pii(text: str) -> str:
    text = _KEYS.sub("[REDACTED API KEY]", text)
    text = _SSN.sub("[REDACTED SSN]", text)
    return _CARD.sub(lambda m: "[REDACTED CREDIT CARD]" if luhn_valid(m.group(0)) else m.group(0), text)
