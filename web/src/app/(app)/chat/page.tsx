"use client";
import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Hash, Lock } from "lucide-react";
import { api, ApiError, type Channel, type ChatMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useLive } from "@/lib/realtime";
import { fmtTime, initials } from "@/lib/format";
import { ErrorBanner, Empty } from "@/components/ui";

export default function ChatPage() {
  const { me } = useAuth();
  const [channels, setChannels] = useState<Channel[]>([]);
  const [current, setCurrent] = useState<string>("company");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const currentRef = useRef(current);
  currentRef.current = current;

  useEffect(() => { api<Channel[]>("/channels").then(setChannels).catch((e) => setError(e.message)); }, []);
  useEffect(() => {
    setMessages([]);
    api<ChatMessage[]>(`/channels/${current}/messages`).then(setMessages).catch((e) => setError(e.message));
  }, [current]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [messages]);

  useLive(useCallback((e) => {
    if (e.type !== "chat.message") return;
    const m = e.message as ChatMessage;
    if (m.channel === currentRef.current) setMessages((prev) => (prev.some((x) => x.id === m.id) ? prev : [...prev, m]));
  }, []));

  const ch = channels.find((c) => c.id === current);

  const send = async (e?: FormEvent) => {
    e?.preventDefault();
    const body = draft.trim();
    if (!body || sending) return;
    setSending(true); setError(null);
    try {
      const m = await api<ChatMessage>(`/channels/${current}/messages`, { body: { body } });
      setMessages((prev) => (prev.some((x) => x.id === m.id) ? prev : [...prev, m]));
      setDraft("");
    } catch (err) { setError(err instanceof ApiError ? err.detail : "Could not send."); } finally { setSending(false); }
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };

  return (
    <div className="flex h-full flex-col md:flex-row">
      <div className="shrink-0 border-b border-line md:w-56 md:border-b-0 md:border-r">
        <div className="px-4 py-3 text-xs font-medium uppercase tracking-wide text-mute">Channels</div>
        <ul className="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-col" aria-label="Channels">
          {channels.map((c) => (
            <li key={c.id}>
              <button onClick={() => setCurrent(c.id)} aria-current={c.id === current ? "true" : undefined}
                className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm ${c.id === current ? "bg-brand/15" : "text-mute hover:bg-panel2 hover:text-ink"}`}>
                {c.kind === "department" ? <Lock size={14} /> : <Hash size={14} />} {c.name}
              </button>
            </li>
          ))}
        </ul>
        <p className="hidden px-4 py-2 text-xs text-mute md:block">To reach another department, use <b>Pings</b>: ask the department, not a person.</p>
      </div>

      <section className="flex min-h-0 flex-1 flex-col" aria-label="Conversation">
        <header className="border-b border-line px-4 py-3">
          <h1 className="font-semibold"># {ch?.name ?? current}</h1>
          <p className="text-xs text-mute">{ch?.kind === "department" ? `Private to the ${ch.department} department` : "Everyone in the company can read this"}</p>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3" role="log" aria-live="polite">
          {messages.length === 0 && <Empty>No messages yet. Say hello.</Empty>}
          <ul className="space-y-3">
            {messages.map((m) => (
              <li key={m.id} className="flex gap-3">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-panel2 text-xs font-semibold">{initials(m.author)}</div>
                <div className="min-w-0">
                  <div className="text-sm"><b>{m.author ?? "unknown"}</b>{m.author_id === me?.id && <span className="ml-1 text-xs text-mute">(you)</span>} <span className="ml-1 text-xs text-mute">{fmtTime(m.created_at)}</span></div>
                  <p className="whitespace-pre-wrap break-words text-sm">{m.body}</p>
                </div>
              </li>
            ))}
          </ul>
          <div ref={bottom} />
        </div>
        <form onSubmit={send} className="space-y-2 border-t border-line p-3">
          <ErrorBanner error={error} />
          <div className="flex gap-2">
            <textarea className="input min-h-[44px] flex-1 resize-none" rows={1} placeholder={ch?.can_post === false ? "You can read but not post here" : `Message #${ch?.name ?? ""}`}
              value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey} disabled={ch?.can_post === false} aria-label="Message" />
            <button className="btn btn-primary" disabled={sending || !draft.trim() || ch?.can_post === false}>Send</button>
          </div>
        </form>
      </section>
    </div>
  );
}
