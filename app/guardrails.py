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
from typing import List, Tuple

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
# A run of digits optionally separated by single spaces / hyphens, not glued to letters.
_DIGIT_RUN = re.compile(r"(?<!\w)\d(?:[ -]?\d)*(?!\w)")
_CARD_LENGTHS = (16, 15, 14, 13, 17, 18, 19)  # most common first
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


def pii_spans(text: str) -> List[Tuple[int, int, str]]:
    """Sensitive spans as (start, end, replacement), applied in the same order as before:
    keys, then SSNs, then Luhn-valid card numbers. Earlier matches are blanked out so later
    patterns cannot match inside them."""
    spans: List[Tuple[int, int, str]] = []
    work = text
    for regex, label in ((_KEYS, "[REDACTED API KEY]"), (_SSN, "[REDACTED SSN]")):
        for m in list(regex.finditer(work)):
            spans.append((m.start(), m.end(), label))
            work = work[:m.start()] + "\x00" * (m.end() - m.start()) + work[m.end():]
    spans.extend(_card_spans(work, "[REDACTED CREDIT CARD]"))
    return sorted(spans)


def plausible_card(digits: str) -> bool:
    """Luhn-valid AND a real issuer range and length. Luhn alone passes ~1 in 10 random numbers
    (timestamps, order ids), so a known prefix keeps ordinary numbers from being masked."""
    n = len(digits)
    if not luhn_valid(digits):
        return False
    if digits[0] == "4":
        return n in (13, 16, 19)
    two = int(digits[:2])
    if 51 <= two <= 55 or 2221 <= int(digits[:4]) <= 2720:
        return n == 16
    if two in (34, 37):
        return n == 15
    if digits[:4] == "6011" or two == 65 or 644 <= int(digits[:3]) <= 649 or two == 62:
        return 16 <= n <= 19
    if two == 35:
        return 16 <= n <= 19
    if 300 <= int(digits[:3]) <= 305 or two in (36, 38, 39):
        return n == 14
    return False


def _card_spans(work: str, label: str) -> List[Tuple[int, int, str]]:
    """Cards in digit runs. Only the start of a run (or right after a card) is tried, so a long
    numeric id is never scanned with a sliding window that would find chance matches; adjacent
    cards are peeled off one at a time; separators are never swallowed."""
    spans: List[Tuple[int, int, str]] = []
    for run in _DIGIT_RUN.finditer(work):
        text = run.group(0)
        pos = [i for i, c in enumerate(text) if c.isdigit()]
        digits = "".join(text[i] for i in pos)
        i = 0
        while i < len(digits):
            for n in _CARD_LENGTHS:
                if i + n <= len(digits) and plausible_card(digits[i:i + n]):
                    spans.append((run.start() + pos[i], run.start() + pos[i + n - 1] + 1, label))
                    i += n
                    break
            else:
                break
    return spans


def mask_pii(text: str) -> str:
    out, last = [], 0
    for start, end, label in pii_spans(text):
        out.append(text[last:start])
        out.append(label)
        last = end
    out.append(text[last:])
    return "".join(out)


class StreamMasker:
    """Applies ``mask_pii`` to text that arrives in small pieces (LLM tokens).

    A secret can be split across tokens (a card number "4111 1111 1111 1111" is four of them),
    so text is only released once it is safely behind the stream head: we always keep an
    unfinished tail, never cut inside a whitespace-delimited token, never split a detected
    span, and hold an in-progress PEM private-key block until it ends. Concatenating every
    ``feed`` result plus ``flush`` equals ``mask_pii`` of the whole text.
    """

    LOOKBACK = 40  # longer than any multi-token secret (a spaced card number is 23 chars)

    def __init__(self) -> None:
        self.pending = ""

    def feed(self, piece: str) -> str:
        self.pending += piece
        text = self.pending
        safe = len(text) - self.LOOKBACK
        begin = text.rfind("-----BEGIN")
        if begin != -1 and "-----END" not in text[begin:]:
            safe = min(safe, begin)
        if safe <= 0:
            return ""
        cut = max(text.rfind(" ", 0, safe), text.rfind("\n", 0, safe), text.rfind("\t", 0, safe))
        if cut <= 0:
            return ""
        for start, end, _ in pii_spans(text):
            if start < cut < end:
                cut = start
        if cut <= 0:
            return ""
        head, self.pending = text[:cut], text[cut:]
        return mask_pii(head)

    def flush(self) -> str:
        out, self.pending = mask_pii(self.pending), ""
        return out
