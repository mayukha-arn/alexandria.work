"""Claude as Alexandria's language model (hosted), via the official Anthropic SDK.

Same interface as the local OllamaLLM: ``stream(messages)`` yields text pieces and exposes ``.usage`` once
finished; ``complete(messages)`` returns a Completion. ``messages`` uses Alexandria's internal shape (a
leading system message, then the user turn), which is converted to the Messages API shape here.

Refusals are rerouted server-side with ``fallbacks: "default"``; if the whole chain still declines, the
answer is a short neutral sentence instead of an empty bubble.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Iterator, List, Optional

from .llm import Completion

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
DECLINED = "I can't help with that request."


def _split(messages: List[Dict[str, str]]):
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    turns = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
    return system, turns


class ClaudeStream:
    def __init__(self, llm: "ClaudeLLM", messages: List[Dict[str, str]]) -> None:
        self.llm, self.messages, self.usage = llm, messages, {}
        self.stop_reason: Optional[str] = None

    def __iter__(self) -> Iterator[str]:
        system, turns = _split(self.messages)
        sent_any = False
        with self.llm.client.beta.messages.stream(
            model=self.llm.model,
            max_tokens=self.llm.max_tokens,
            system=system,
            messages=turns,
            output_config={"effort": self.llm.effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            for text in stream.text_stream:
                if text:
                    sent_any = True
                    yield text
            final = stream.get_final_message()
        self.stop_reason = final.stop_reason
        self.usage = {"prompt_tokens": final.usage.input_tokens, "completion_tokens": final.usage.output_tokens}
        if final.stop_reason == "refusal" and not sent_any:
            yield DECLINED


class ClaudeLLM:
    def __init__(self, client: Any = None, model: Optional[str] = None, effort: Optional[str] = None,
                 max_tokens: int = 4000) -> None:
        if client is None:
            import anthropic
            client = anthropic.Anthropic()          # credentials from ANTHROPIC_API_KEY (pulled from Key Vault)
        self.client = client
        self.model = model or os.getenv("ALEXANDRIA_CLAUDE_MODEL", DEFAULT_MODEL)
        # low effort keeps chat answers quick; raise it with ALEXANDRIA_CLAUDE_EFFORT if quality needs it
        self.effort = effort or os.getenv("ALEXANDRIA_CLAUDE_EFFORT", "low")
        self.max_tokens = max_tokens

    def stream(self, messages: List[Dict[str, str]]) -> ClaudeStream:
        return ClaudeStream(self, messages)

    def complete(self, messages: List[Dict[str, str]]) -> Completion:
        start, first, parts = time.perf_counter(), None, []
        s = self.stream(messages)
        for piece in s:
            if first is None:
                first = (time.perf_counter() - start) * 1000
            parts.append(piece)
        return Completion("".join(parts), first, (time.perf_counter() - start) * 1000,
                          s.usage.get("prompt_tokens"), s.usage.get("completion_tokens"))
