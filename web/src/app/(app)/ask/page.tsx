"use client";
import { useRef, useState, type FormEvent } from "react";
import { BadgeCheck, ShieldAlert } from "lucide-react";
import { ApiError, type AskDone } from "@/lib/api";
import { streamPost } from "@/lib/sse";
import { useAuth } from "@/lib/auth";
import { Badge, ErrorBanner, Notice, Page } from "@/components/ui";

const PERSONA: Record<string, string> = {
  support: "Plain-language steps and a ready-to-send customer message",
  developer: "Technical detail: exact paths, parameters, error codes",
  executive: "A short summary with a risk and SLA callout",
};
const VERIFY: Record<string, { tone: "good" | "warn" | "bad" | "neutral"; text: string }> = {
  verified: { tone: "good", text: "Verified on-chain" },
  unanchored: { tone: "warn", text: "Not yet anchored on-chain" },
  unchecked: { tone: "warn", text: "Could not check on-chain" },
  tampered: { tone: "bad", text: "Failed integrity check" },
};

type Turn = { question: string; text: string; done: AskDone | null; warnings: string[]; error: string | null; streaming: boolean };

export default function AskPage() {
  const { me } = useAuth();
  const [q, setQ] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const abort = useRef<AbortController | null>(null);
  const busy = turns.at(-1)?.streaming ?? false;

  const update = (patch: Partial<Turn>) => setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, ...patch } : x)));

  const ask = async (e: FormEvent) => {
    e.preventDefault();
    const question = q.trim();
    if (!question || busy) return;
    setQ("");
    setTurns((t) => [...t, { question, text: "", done: null, warnings: [], error: null, streaming: true }]);
    abort.current = new AbortController();
    let acc = "";
    try {
      await streamPost("/ask/stream", { question }, (ev) => {
        if (ev.event === "meta") update({ warnings: ev.data.warnings });
        else if (ev.event === "token") { acc += ev.data.text; update({ text: acc }); }
        else if (ev.event === "done") update({ done: ev.data, text: ev.data.answer, warnings: ev.data.warnings, streaming: false });
        else if (ev.event === "error") update({ error: ev.data.detail, streaming: false });
      }, abort.current.signal);
      update({ streaming: false });
    } catch (err) {
      update({ error: err instanceof ApiError ? err.detail : "Something went wrong.", streaming: false });
    }
  };

  return (
    <Page title="Ask Alexandria" subtitle={`Answers come only from documents you're cleared to read, written for your role (${me?.persona}): ${PERSONA[me?.persona ?? "support"]}.`}>
      <div className="space-y-4" aria-live="polite">
        {turns.length === 0 && <Notice tone="brand">Ask anything about your company's documented knowledge. If the documents don't contain the answer, Alexandria says so instead of guessing.</Notice>}
        {turns.map((t, i) => (
          <article key={i} className="space-y-2">
            <div className="ml-auto max-w-[85%] rounded-xl bg-brand/15 px-3 py-2 text-sm">{t.question}</div>
            <div className="card space-y-3 p-4">
              <ErrorBanner error={t.error} />
              {t.warnings.map((w, k) => <Notice key={k}><ShieldAlert size={14} className="mr-1 inline" />{w}</Notice>)}
              {t.text ? <p className="whitespace-pre-wrap text-sm leading-relaxed" data-testid="answer">{t.text}{t.streaming && <span className="animate-pulse">▍</span>}</p> : t.streaming && <p className="text-sm text-mute">Searching your documents…</p>}
              {t.done && (
                <div className="space-y-2 border-t border-line pt-3">
                  <div className="flex flex-wrap items-center gap-2 text-xs text-mute">
                    {t.done.grounded ? <Badge tone="good"><BadgeCheck size={12} className="mr-1" />Grounded in documents</Badge> : <Badge tone="warn">Not tied to documents: treat as unverified</Badge>}
                    <Badge>{t.done.persona} view</Badge>
                    {t.done.metrics.ttft_ms != null && <span>first words in {Math.round(t.done.metrics.ttft_ms)} ms</span>}
                  </div>
                  {t.done.sources.length > 0 && (
                    <ul className="space-y-1" aria-label="Sources">
                      {t.done.sources.map((s) => (
                        <li key={s.chunk_id} className="flex flex-wrap items-center gap-2 text-sm">
                          <span className="font-mono text-xs text-mute">[{s.n}]</span><span>{s.source || "document"}</span>
                          {s.department && <Badge>{s.department}</Badge>}
                          {s.verification && VERIFY[s.verification] && <Badge tone={VERIFY[s.verification].tone}>{VERIFY[s.verification].text}</Badge>}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
          </article>
        ))}
      </div>
      <form onSubmit={ask} className="sticky bottom-0 -mx-1 flex gap-2 bg-bg/90 p-1 backdrop-blur" aria-label="Ask a question">
        <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. How do I process a refund over $100?" maxLength={2000} aria-label="Question" />
        <button className="btn btn-primary" disabled={busy || !q.trim()}>{busy ? "Answering…" : "Ask"}</button>
      </form>
    </Page>
  );
}
