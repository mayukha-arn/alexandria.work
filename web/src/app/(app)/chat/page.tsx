"use client";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import Link from "next/link";
import { AtSign, ChevronDown, FileText, Hash, Inbox, Lock, Paperclip, SendHorizonal, SmilePlus, Sparkles, X } from "lucide-react";
import { api, ApiError, type Channel, type ChatMessage, type Ping } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useLive } from "@/lib/realtime";
import { allowedLabels } from "@/lib/format";
import { displayName, ORG_NAME } from "@/lib/people";

import { AiAnswer } from "@/components/ai-answer";
import { AiAvatar, Avatar } from "@/components/avatar";
import { ErrorBanner, Field } from "@/components/ui";

type Extra =
  | { kind: "ai"; question: string }
  | { kind: "ping"; ping: Ping }
  | { kind: "file"; name: string; status: string; level: string };

const REACTIONS = ["👍", "✅", "👀", "🎉"];
const STATUS_TONE: Record<string, string> = { open: "bg-warn/10 text-warn", claimed: "bg-brand/10 text-brand", answered: "bg-good/10 text-good", resolved: "bg-panel2 text-mute", closed: "bg-panel2 text-mute" };

function dayLabel(ts: number) {
  const d = new Date(ts * 1000), today = new Date();
  const y = new Date(); y.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === y.toDateString()) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
}
const clock = (ts: number) => new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

export default function ChatPage() {
  const { me, meta } = useAuth();
  const [channels, setChannels] = useState<Channel[]>([]);
  const [current, setCurrent] = useState<string>("company");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [extras, setExtras] = useState<Record<number, Extra[]>>({});
  const [reactions, setReactions] = useState<Record<number, Record<string, number>>>({});
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const currentRef = useRef(current);
  currentRef.current = current;
  const departments = meta?.departments ?? [];

  useEffect(() => { api<Channel[]>("/channels").then(setChannels).catch((e) => setError(e.message)); }, []);
  useEffect(() => {
    setMessages([]);
    api<ChatMessage[]>(`/channels/${current}/messages?limit=100`).then(setMessages).catch((e) => setError(e.message));
  }, [current]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [messages, extras]);

  useLive(useCallback((e) => {
    if (e.type === "chat.message") {
      const m = e.message as ChatMessage;
      if (m.channel === currentRef.current) setMessages((prev) => (prev.some((x) => x.id === m.id) ? prev : [...prev, m]));
    }
    if (e.type.startsWith("ping.") && e.ping) {
      setExtras((prev) => {
        const next = { ...prev };
        for (const id of Object.keys(next)) next[+id] = next[+id].map((x) => (x.kind === "ping" && x.ping.id === e.ping.id ? { ...x, ping: { ...x.ping, ...e.ping } } : x));
        return next;
      });
    }
  }, []));

  const ch = channels.find((c) => c.id === current);
  const addExtra = (id: number, x: Extra) => setExtras((p) => ({ ...p, [id]: [...(p[id] ?? []), x] }));

  const post = async (body: string) => {
    const m = await api<ChatMessage>(`/channels/${current}/messages`, { body: { body } });
    setMessages((prev) => (prev.some((x) => x.id === m.id) ? prev : [...prev, m]));
    return m;
  };

  const send = async (e?: FormEvent) => {
    e?.preventDefault();
    const body = draft.trim();
    if (!body || sending) return;
    setSending(true); setError(null);
    try {
      const m = await post(body);
      setDraft("");
      const mentions = [...body.matchAll(/@([a-z][\w-]*)/gi)].map((x) => x[1].toLowerCase());
      const clean = body.replace(/@[a-z][\w-]*/gi, "").replace(/\s+/g, " ").trim();
      if (mentions.includes("alexandria") && clean) addExtra(m.id, { kind: "ai", question: clean });
      const dept = mentions.find((x) => departments.includes(x));
      if (dept && clean) {
        const ping = await api<Ping>("/pings", { body: { to_department: dept, title: clean.slice(0, 120), body: clean, min_role: "public" } });
        addExtra(m.id, { kind: "ping", ping });
      }
    } catch (err) { setError(err instanceof ApiError ? err.detail : "Could not send."); } finally { setSending(false); }
  };

  // @-mention suggestions for the word being typed
  const mention = /(^|\s)@([a-z-]*)$/i.exec(draft);
  const options = mention ? ["alexandria", ...departments.filter((d) => d !== me?.department)].filter((o) => o.startsWith(mention[2].toLowerCase())) : [];
  const [optIdx, setOptIdx] = useState(0);
  const pick = (o: string) => { setDraft((d) => d.replace(/@([a-z-]*)$/i, `@${o} `)); setOptIdx(0); box.current?.focus(); };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (options.length) {
      if (e.key === "ArrowDown") { e.preventDefault(); setOptIdx((i) => (i + 1) % options.length); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setOptIdx((i) => (i - 1 + options.length) % options.length); return; }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) { e.preventDefault(); pick(options[Math.min(optIdx, options.length - 1)]); return; }
    }
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  };

  const groups = useMemo(() => {
    const out: { day: string; items: { m: ChatMessage; head: boolean }[] }[] = [];
    let prev: ChatMessage | null = null;
    for (const m of messages) {
      const day = dayLabel(m.created_at);
      if (!out.length || out[out.length - 1].day !== day) { out.push({ day, items: [] }); prev = null; }
      const head = !prev || prev.author_id !== m.author_id || m.created_at - prev.created_at > 300 || !!extras[prev.id]?.length;
      out[out.length - 1].items.push({ m, head });
      prev = m;
    }
    return out;
  }, [messages, extras]);

  const react = (id: number, r: string) => setReactions((p) => ({ ...p, [id]: { ...(p[id] ?? {}), [r]: ((p[id] ?? {})[r] ?? 0) + 1 } }));

  return (
    <div className="flex h-full min-h-0 flex-col md:flex-row">
      {/* channel sidebar */}
      <aside className="flex shrink-0 flex-col bg-side text-side-ink md:w-64">
        <div className="flex items-center justify-between px-4 py-3">
          <div className="flex items-center gap-1 font-semibold">{ORG_NAME} <ChevronDown size={14} className="text-side-mute" /></div>
        </div>
        <div className="scroll-thin flex gap-4 overflow-x-auto px-2 pb-3 md:flex-1 md:flex-col md:overflow-y-auto">
          <Section title="Channels">
            <ul className="flex gap-0.5 md:flex-col" aria-label="Channels">
              {channels.map((c) => (
                <li key={c.id}>
                  <button onClick={() => setCurrent(c.id)} aria-current={c.id === current ? "true" : undefined}
                    className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[14px] ${c.id === current ? "bg-side-active font-semibold text-white" : "text-side-mute hover:bg-side-hover hover:text-side-ink"}`}>
                    {c.kind === "department" ? <Lock size={14} /> : <Hash size={14} />} {c.name}
                  </button>
                </li>
              ))}
            </ul>
          </Section>
          <Section title="Ask a department">
            <ul className="flex gap-0.5 md:flex-col">
              {departments.filter((d) => d !== me?.department).map((d) => (
                <li key={d}>
                  <button onClick={() => { setDraft((x) => `@${d} ${x}`); box.current?.focus(); }} className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[14px] capitalize text-side-mute hover:bg-side-hover hover:text-side-ink">
                    <Inbox size={14} /> {d}
                  </button>
                </li>
              ))}
            </ul>
          </Section>
          <Section title="Apps">
            <button onClick={() => { setDraft((x) => `@alexandria ${x}`); box.current?.focus(); }} className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[14px] text-side-mute hover:bg-side-hover hover:text-side-ink">
              <Sparkles size={14} className="text-brand2" /> Alexandria
            </button>
          </Section>
        </div>
      </aside>

      {/* conversation */}
      <section className="flex min-h-0 flex-1 flex-col bg-white" aria-label="Conversation">
        <header className="flex items-center justify-between border-b border-line px-5 py-2.5">
          <div className="min-w-0">
            <h1 className="flex items-center gap-1.5 text-[17px] font-bold">{ch?.kind === "department" ? <Lock size={15} /> : <Hash size={16} />}{ch?.name ?? current}</h1>
            <p className="truncate text-xs text-mute">{ch?.kind === "department" ? `Private to ${ch.department}` : "Company-wide announcements and conversation"} · mention <b>@alexandria</b> to ask the knowledge base, or <b>@department</b> to ask a team</p>
          </div>
        </header>

        <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-5 py-4" role="log" aria-live="polite">
          {messages.length === 0 && <EmptyChannel name={ch?.name ?? current} />}
          {groups.map((g) => (
            <div key={g.day}>
              <div className="sticky top-0 z-10 my-3 flex justify-center"><span className="rounded-full border border-line bg-white px-3 py-0.5 text-[11px] font-semibold text-mute shadow-sm">{g.day}</span></div>
              {g.items.map(({ m, head }) => (
                <div key={m.id}>
                  <div className={`group relative -mx-2 flex gap-3 rounded-lg px-2 hover:bg-panel2/70 ${head ? "mt-3 pt-1" : ""}`}>
                    <div className="w-9 shrink-0">{head ? <Avatar name={m.author} /> : <span className="hidden pt-1 text-[10px] text-mute group-hover:block">{clock(m.created_at)}</span>}</div>
                    <div className="min-w-0 flex-1 pb-0.5">
                      {head && <div className="flex items-baseline gap-2"><span className="text-[15px] font-semibold">{displayName(m.author)}</span>{m.author_id === me?.id && <span className="text-[11px] text-mute">(you)</span>}<span className="text-[11px] text-mute">{clock(m.created_at)}</span></div>}
                      <p className="whitespace-pre-wrap break-words text-[15px] leading-relaxed">{highlight(m.body)}</p>
                      {reactions[m.id] && (
                        <div className="mt-1 flex gap-1">{Object.entries(reactions[m.id]).map(([r, n]) => <button key={r} onClick={() => react(m.id, r)} className="rounded-full border border-brand/30 bg-brand/5 px-1.5 text-xs">{r} {n}</button>)}</div>
                      )}
                    </div>
                    <div className="absolute -top-3 right-2 hidden gap-0.5 rounded-lg border border-line bg-white p-0.5 shadow-sm group-hover:flex">
                      {REACTIONS.map((r) => <button key={r} onClick={() => react(m.id, r)} className="rounded px-1 text-sm hover:bg-panel2" aria-label={`React ${r}`}>{r}</button>)}
                      <span className="flex items-center px-1 text-mute"><SmilePlus size={14} /></span>
                    </div>
                  </div>
                  {(extras[m.id] ?? []).map((x, i) => (
                    <div key={i} className="ml-12 mt-2">
                      {x.kind === "ai" && <AiAnswer question={x.question} compact />}
                      {x.kind === "ping" && <PingCard ping={x.ping} />}
                      {x.kind === "file" && <FileCard name={x.name} status={x.status} level={x.level} />}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          ))}
          <div ref={bottom} />
        </div>

        <form onSubmit={send} className="relative px-5 pb-4 pt-2">
          <ErrorBanner error={error} />
          {!!options.length && (
            <ul className="animate-in absolute bottom-full left-5 mb-1 w-72 overflow-hidden rounded-xl border border-line bg-white py-1 shadow-xl" role="listbox" aria-label="Mention">
              {options.map((o, i) => (
                <li key={o}>
                  <button type="button" onMouseDown={(e) => { e.preventDefault(); pick(o); }} className={`flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm ${i === optIdx % options.length ? "bg-brand/10" : ""}`}>
                    {o === "alexandria" ? <><AiAvatar size={22} /><span><b>Alexandria</b> <span className="text-mute">· answers from your documents</span></span></>
                      : <><span className="flex h-[22px] w-[22px] items-center justify-center rounded-md bg-panel2 text-mute"><Inbox size={13} /></span><span><b className="capitalize">{o}</b> <span className="text-mute">· anyone qualified can answer</span></span></>}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="rounded-xl border border-line bg-white shadow-sm focus-within:border-brand/60 focus-within:ring-2 focus-within:ring-brand/10">
            <textarea ref={box} className="block max-h-40 min-h-[48px] w-full resize-none bg-transparent px-3.5 pt-3 text-[15px] outline-none placeholder:text-mute/70" rows={1}
              placeholder={ch?.can_post === false ? "You can read but not post here" : `Message #${ch?.name ?? ""}`}
              value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey} disabled={ch?.can_post === false} aria-label="Message" />
            <div className="flex items-center justify-between px-2 pb-2">
              <div className="flex items-center gap-0.5 text-mute">
                <button type="button" onClick={() => setUploading(true)} className="rounded-md p-1.5 hover:bg-panel2 hover:text-ink" aria-label="Attach a document"><Paperclip size={17} /></button>
                <button type="button" onClick={() => { setDraft((d) => (d && !d.endsWith(" ") ? d + " @" : d + "@")); box.current?.focus(); }} className="rounded-md p-1.5 hover:bg-panel2 hover:text-ink" aria-label="Mention"><AtSign size={17} /></button>
                <button type="button" onClick={() => { setDraft((d) => `@alexandria ${d}`); box.current?.focus(); }} className="flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium hover:bg-brand/10 hover:text-brand"><Sparkles size={14} /> Ask Alexandria</button>
              </div>
              <button className="ai-gradient flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-semibold text-white shadow-sm disabled:opacity-40" disabled={sending || !draft.trim() || ch?.can_post === false} aria-label="Send">
                <SendHorizonal size={15} /> Send
              </button>
            </div>
          </div>
        </form>
      </section>

      {uploading && <UploadDialog onClose={() => setUploading(false)} onDone={async (name, status, level) => {
        setUploading(false);
        try { const m = await post(`📎 Shared ${name}`); addExtra(m.id, { kind: "file", name, status, level }); } catch {}
      }} />}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="shrink-0">
      <div className="mb-1 hidden px-2.5 text-[11px] font-semibold uppercase tracking-wider text-side-mute/80 md:block">{title}</div>
      {children}
    </div>
  );
}

function highlight(text: string) {
  return text.split(/(@[a-z][\w-]*)/gi).map((part, i) =>
    /^@[a-z]/i.test(part)
      ? <span key={i} className={`rounded px-1 font-medium ${part.toLowerCase() === "@alexandria" ? "bg-brand/10 text-brand" : "bg-brand2/10 text-brand2"}`}>{part}</span>
      : part);
}

function EmptyChannel({ name }: { name: string }) {
  return (
    <div className="mx-auto max-w-md py-16 text-center">
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-brand/10 text-brand"><Hash /></div>
      <h2 className="mt-3 text-lg font-semibold">Welcome to #{name}</h2>
      <p className="mt-1 text-sm text-mute">Say hello, mention <b>@alexandria</b> to search the knowledge base, or mention a department to get an answer from the right team.</p>
    </div>
  );
}

function PingCard({ ping }: { ping: Ping }) {
  return (
    <Link href={`/pings/?id=${ping.id}`} className="animate-in flex max-w-lg items-center gap-3 rounded-xl border border-line bg-white px-3.5 py-2.5 shadow-sm transition hover:border-brand/40">
      <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand2/10 text-brand2"><Inbox size={18} /></span>
      <div className="min-w-0 flex-1">
        <div className="text-xs text-mute">Sent to <b className="capitalize text-ink">{ping.to_department}</b> · anyone qualified can pick it up</div>
        <div className="truncate text-sm font-medium">{ping.title}</div>
      </div>
      <span className={`rounded-full px-2 py-0.5 text-xs font-semibold capitalize ${STATUS_TONE[ping.status] ?? ""}`}>{ping.status}{ping.claimed_by ? ` · ${displayName(ping.claimed_by)}` : ""}</span>
    </Link>
  );
}

function FileCard({ name, status, level }: { name: string; status: string; level: string }) {
  const label = status === "pending_approval" ? "In review" : status.includes("ingested") ? "Live" : status === "duplicate_skipped" ? "Already in the library" : "Not added";
  return (
    <Link href="/documents/" className="animate-in flex max-w-lg items-center gap-3 rounded-xl border border-line bg-white px-3.5 py-2.5 shadow-sm transition hover:border-brand/40">
      <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand/10 text-brand"><FileText size={18} /></span>
      <div className="min-w-0 flex-1"><div className="truncate text-sm font-medium">{name}</div><div className="text-xs text-mute">Knowledge base · visible to <b>{level}</b></div></div>
      <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${label === "Live" ? "bg-good/10 text-good" : label === "In review" ? "bg-warn/10 text-warn" : "bg-panel2 text-mute"}`}>{label}</span>
    </Link>
  );
}

function UploadDialog({ onClose, onDone }: { onClose: () => void; onDone: (name: string, status: string, level: string) => void }) {
  const { me, meta } = useAuth();
  const labels = allowedLabels(meta!.classifications, me!.clearance);
  const [file, setFile] = useState<File | null>(null);
  const [level, setLevel] = useState(labels.includes("internal") ? "internal" : labels[0]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!file) return;
    setBusy(true); setError(null);
    const form = new FormData();
    form.append("file", file); form.append("min_role", level); form.append("department", me!.department);
    try { const r = await api<{ status: string }>("/documents", { form }); onDone(file.name, r.status, level); }
    catch (err) { setError(err instanceof ApiError ? err.detail : "Upload failed."); } finally { setBusy(false); }
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#06291f]/40 p-4 backdrop-blur-sm" onClick={onClose}>
      <form onSubmit={submit} onClick={(e) => e.stopPropagation()} className="animate-in w-full max-w-md space-y-4 rounded-2xl border border-line bg-white p-5 shadow-2xl" aria-label="Share a document">
        <div className="flex items-center justify-between"><h2 className="text-lg font-semibold">Share a document</h2><button type="button" onClick={onClose} aria-label="Close" className="rounded-md p-1 text-mute hover:bg-panel2"><X size={18} /></button></div>
        <p className="text-sm text-mute">It joins the knowledge base once a colleague approves it, and Alexandria can then use it to answer questions.</p>
        <ErrorBanner error={error} />
        <label className={`flex cursor-pointer flex-col items-center justify-center gap-1 rounded-xl border-2 border-dashed px-4 py-6 text-center text-sm transition ${file ? "border-brand/50 bg-brand/5" : "border-line hover:border-brand/40"}`}>
          <FileText className="text-brand" />
          <span className="font-medium">{file ? file.name : "Choose a PDF"}</span>
          <span className="text-xs text-mute">{file ? `${(file.size / 1024).toFixed(0)} KB` : "Text PDFs up to 25 MB"}</span>
          <input type="file" accept="application/pdf,.pdf" className="sr-only" onChange={(e) => setFile(e.target.files?.[0] ?? null)} aria-label="PDF" />
        </label>
        <Field label="Who may read it"><select className="input" value={level} onChange={(e) => setLevel(e.target.value)}>{labels.map((l) => <option key={l} value={l}>{l} (level {meta!.classifications[l]})</option>)}</select></Field>
        <div className="flex justify-end gap-2"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={!file || busy}>{busy ? "Uploading…" : "Share"}</button></div>
      </form>
    </div>
  );
}

