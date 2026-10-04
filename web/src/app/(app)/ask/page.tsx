"use client";
import { Suspense, useEffect, useRef, useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import { ArrowUp, Sparkles } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { displayName } from "@/lib/people";
import { AiAnswer } from "@/components/ai-answer";
import { Avatar } from "@/components/avatar";

const SUGGESTIONS: Record<string, string[]> = {
  support: ["How do I handle a refund over $100?", "A customer's paycheck is missing overtime. What do I tell them?", "What's our response time commitment for payroll errors?"],
  developer: ["What should I do when checkout returns a 500 error?", "How do I rotate the payroll API keys?", "What are the rate limits on the payroll API?"],
  executive: ["Summarise our open compliance risks", "What is our SLA for payroll corrections?", "Which teams depend on the payroll API?"],
};

export default function AskPage() {
  return <Suspense fallback={null}><Ask /></Suspense>;
}

function Ask() {
  const { me } = useAuth();
  const params = useSearchParams();
  const [q, setQ] = useState("");
  const [turns, setTurns] = useState<string[]>([]);
  const bottom = useRef<HTMLDivElement>(null);
  const seeded = useRef(false);

  useEffect(() => {
    const initial = params.get("q");
    if (initial && !seeded.current) { seeded.current = true; setTurns([initial]); }
  }, [params]);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [turns]);

  const ask = (e?: FormEvent, text?: string) => {
    e?.preventDefault();
    const question = (text ?? q).trim();
    if (!question) return;
    setQ("");
    setTurns((t) => [...t, question]);
  };
  const ideas = SUGGESTIONS[me?.persona ?? "support"] ?? SUGGESTIONS.support;

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-4 py-8">
          {turns.length === 0 && (
            <div className="animate-in py-10 text-center">
              <div className="ai-gradient mx-auto flex h-14 w-14 items-center justify-center rounded-2xl text-white shadow-lg"><Sparkles size={26} /></div>
              <h1 className="mt-4 text-2xl font-semibold tracking-tight">Ask Alexandria</h1>
              <p className="mx-auto mt-2 max-w-md text-sm text-mute">Answers come only from documents you're cleared to read, written for your role, with the sources to prove it.</p>
              <div className="mx-auto mt-6 grid max-w-xl gap-2">
                {ideas.map((s) => (
                  <button key={s} onClick={() => ask(undefined, s)} className="rounded-xl border border-line bg-white px-4 py-3 text-left text-sm shadow-sm transition hover:border-brand/40 hover:shadow">{s}</button>
                ))}
              </div>
            </div>
          )}
          <div className="space-y-6">
            {turns.map((t, i) => (
              <div key={i} className="space-y-4">
                <div className="flex gap-3 animate-in">
                  <Avatar name={me?.username} />
                  <div><div className="font-semibold">{displayName(me?.username)}</div><p className="mt-0.5 text-[14px]">{t}</p></div>
                </div>
                <AiAnswer question={t} />
              </div>
            ))}
          </div>
          <div ref={bottom} />
        </div>
      </div>
      <form onSubmit={ask} className="border-t border-line bg-white px-4 py-3" aria-label="Ask a question">
        <div className="mx-auto flex max-w-3xl items-center gap-2 rounded-2xl border border-line bg-white px-3 py-2 shadow-sm focus-within:border-brand focus-within:ring-2 focus-within:ring-brand/15">
          <Sparkles size={18} className="text-brand" />
          <input className="flex-1 bg-transparent py-1.5 text-[15px] outline-none" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask anything about your company's knowledge…" maxLength={2000} aria-label="Question" />
          <button className="ai-gradient flex h-8 w-8 items-center justify-center rounded-lg text-white disabled:opacity-40" disabled={!q.trim()} aria-label="Ask"><ArrowUp size={16} /></button>
        </div>
      </form>
    </div>
  );
}
