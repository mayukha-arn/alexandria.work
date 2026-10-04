"use client";
import { docTitle } from "@/lib/format";
import { useEffect, useRef, useState } from "react";
import { BadgeCheck, FileText, ShieldAlert, ShieldCheck } from "lucide-react";
import { ApiError, type AskDone, type Source } from "@/lib/api";
import { streamPost } from "@/lib/sse";
import { Markdown } from "@/lib/markdown";
import { AiAvatar } from "./avatar";

const VERIFY: Record<string, { text: string; cls: string }> = {
  verified: { text: "Verified on-chain", cls: "text-good bg-good/10" },
  unanchored: { text: "Approval pending on-chain", cls: "text-warn bg-warn/10" },
  unchecked: { text: "On-chain check unavailable", cls: "text-mute bg-panel2" },
  tampered: { text: "Failed integrity check", cls: "text-bad bg-bad/10" },
};

/** Streams Alexandria's answer to ``question`` (once, on mount) and renders it as a message. */
export function AiAnswer({ question, compact = false }: { question: string; compact?: boolean }) {
  const [text, setText] = useState("");
  const [done, setDone] = useState<AskDone | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [focus, setFocus] = useState<number | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    let acc = "";
    streamPost("/ask/stream", { question }, (ev) => {
      if (ev.event === "meta") setWarnings(ev.data.warnings ?? []);
      else if (ev.event === "token") { acc += ev.data.text; setText(acc); }
      else if (ev.event === "done") { setDone(ev.data); setText(ev.data.answer); setWarnings(ev.data.warnings ?? []); }
      else if (ev.event === "error") setError(ev.data.detail);
    }).catch((e) => setError(e instanceof ApiError ? e.detail : "Alexandria couldn't answer right now."));
  }, [question]);

  const streaming = !done && !error;
  return (
    <div className="flex gap-3 animate-in" data-testid="ai-answer">
      <AiAvatar size={compact ? 32 : 36} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2"><span className="ai-text font-semibold">Alexandria</span><span className="rounded bg-brand/10 px-1.5 text-[10px] font-semibold uppercase tracking-wide text-brand">AI</span></div>
        <div className="mt-1 rounded-xl border border-brand/15 bg-gradient-to-br from-brand/[0.04] to-brand2/[0.04] px-4 py-3">
          {error && <p className="text-sm text-bad" data-testid="error">{error}</p>}
          {warnings.map((w, i) => <p key={i} className="mb-2 flex items-start gap-1.5 text-xs text-warn"><ShieldAlert size={14} className="mt-0.5 shrink-0" />{w}</p>)}
          {!text && streaming && <div className="space-y-2 py-1" aria-label="Thinking"><div className="skeleton h-3 w-3/4 rounded" /><div className="skeleton h-3 w-1/2 rounded" /></div>}
          {text && <div data-testid="answer"><Markdown text={text} onCite={setFocus} />{streaming && <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse rounded-sm bg-brand align-middle" />}</div>}
          {done && (
            <div className="mt-3 space-y-2 border-t border-brand/10 pt-3">
              <div className="flex flex-wrap items-center gap-2 text-xs">
                {done.grounded
                  ? <span className="inline-flex items-center gap-1 rounded-full bg-good/10 px-2 py-0.5 font-medium text-good"><BadgeCheck size={12} />Grounded in documents</span>
                  : <span className="inline-flex items-center gap-1 rounded-full bg-warn/10 px-2 py-0.5 font-medium text-warn"><ShieldAlert size={12} />Not tied to documents: treat as unverified</span>}
                <span className="rounded-full bg-panel2 px-2 py-0.5 text-mute">{done.persona} view</span>
                {done.metrics.ttft_ms != null && <span className="text-mute">first words in {(done.metrics.ttft_ms / 1000).toFixed(1)}s</span>}
              </div>
              {done.sources.length > 0 && (
                <ul className="grid gap-1.5 sm:grid-cols-2" aria-label="Sources">
                  {done.sources.map((s) => <SourceChip key={s.chunk_id} s={s} active={focus === s.n} />)}
                </ul>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function SourceChip({ s, active }: { s: Source; active: boolean }) {
  const v = s.verification ? VERIFY[s.verification] : null;
  return (
    <li className={`flex items-center gap-2 rounded-lg border bg-white px-2.5 py-1.5 text-xs transition ${active ? "border-brand ring-2 ring-brand/15" : "border-line"}`}>
      <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-brand/10 text-[10px] font-bold text-brand">{s.n}</span>
      <FileText size={13} className="shrink-0 text-mute" />
      <span className="min-w-0 flex-1 truncate font-medium" title={s.source}>{docTitle(s.source)}</span>
      {v && <span className={`inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold ${v.cls}`}>{s.verification === "verified" && <ShieldCheck size={11} />}{v.text}</span>}
    </li>
  );
}
