"use client";
import { Suspense, useCallback, useEffect, useState, type FormEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Plus, Sparkles } from "lucide-react";
import { api, ApiError, type Ping, type PingMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useLive } from "@/lib/realtime";
import { allowedLabels, cap, fmtTime, timeAgo } from "@/lib/format";
import { Badge, Empty, ErrorBanner, Field, Notice, Redacted } from "@/components/ui";
import { Avatar } from "@/components/avatar";
import { displayName } from "@/lib/people";

const TONE: Record<string, "neutral" | "good" | "warn" | "brand" | "bad"> = { open: "warn", claimed: "brand", answered: "good", resolved: "neutral", closed: "neutral" };

export default function PingsPage() {
  return <Suspense fallback={<p className="p-6 text-sm text-mute">Loading…</p>}><Pings /></Suspense>;
}

function Pings() {
  const { me, meta } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const selected = params.get("id") ? Number(params.get("id")) : null;
  const [box, setBox] = useState<"inbox" | "sent">("inbox");
  const [finished, setFinished] = useState(false);
  const [list, setList] = useState<Ping[]>([]);
  const [detail, setDetail] = useState<Ping | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadList = useCallback(() => { api<Ping[]>(`/pings?box=${box}&include_closed=${finished}`).then(setList).catch((e) => setError(e.message)); }, [box, finished]);
  const loadDetail = useCallback(() => {
    if (!selected) { setDetail(null); return; }
    api<Ping>(`/pings/${selected}`).then(setDetail).catch((e) => { setDetail(null); setError(e.message); });
  }, [selected]);
  useEffect(loadList, [loadList]);
  useEffect(loadDetail, [loadDetail]);
  useLive((e) => {
    if (!e.type.startsWith("ping.")) return;
    loadList();
    const id = e.ping?.id ?? e.ping_id;
    if (id === selected) loadDetail();
  });

  const open = (id: number | null) => router.push(id ? `/pings/?id=${id}` : "/pings/");

  return (
    <div className="flex h-full flex-col md:flex-row">
      <div className="flex max-h-72 shrink-0 flex-col border-b border-line bg-panel2/60 md:max-h-none md:w-80 md:border-b-0 md:border-r">
        <div className="px-4 pt-4"><h1 className="text-lg font-bold">Pings</h1><p className="text-xs text-mute">Questions to and from departments</p></div>
        <div className="flex items-center justify-between gap-2 px-4 py-3">
          <div role="tablist" className="flex gap-1">
            {(["inbox", "sent"] as const).map((b) => (
              <button key={b} role="tab" aria-selected={box === b} onClick={() => setBox(b)} className={`rounded-lg px-3 py-1 text-sm font-medium ${box === b ? "bg-brand/10 text-brand" : "text-mute hover:text-ink"}`}>{cap(b)}</button>
            ))}
          </div>
          <button className="btn btn-primary" onClick={() => setCreating(true)}><Plus size={14} /> New ping</button>
        </div>
        <label className="flex items-center gap-2 px-4 pb-2 text-xs text-mute"><input type="checkbox" checked={finished} onChange={(e) => setFinished(e.target.checked)} /> Show finished (resolved or withdrawn)</label>
        <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto px-2 pb-2" aria-label={`${box} pings`}>
          {list.length === 0 && <li><Empty>{box === "inbox" ? "Nothing waiting for your department." : "You haven't pinged anyone yet."}</Empty></li>}
          {list.map((p) => (
            <li key={p.id}>
              <button onClick={() => open(p.id)} aria-current={p.id === selected ? "true" : undefined}
                className={`flex w-full gap-2.5 rounded-lg border px-2.5 py-2 text-left transition ${p.id === selected ? "border-brand/40 bg-white shadow-sm" : "border-transparent hover:bg-white"}`}>
                <Avatar name={box === "inbox" ? p.asker : p.to_department} size={32} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2"><span className="truncate text-sm font-semibold">{p.title}</span><Badge tone={TONE[p.status]}>{p.status}</Badge></div>
                  <div className="mt-0.5 truncate text-xs text-mute">{box === "inbox" ? `from ${displayName(p.asker)}` : <>to <span className="capitalize">{p.to_department}</span></>} · {timeAgo(p.updated_at)}</div>
                </div>
              </button>
            </li>
          ))}
        </ul>
      </div>

      <section className="min-h-0 flex-1 overflow-y-auto" aria-label="Ping">
        {creating && <NewPing onClose={() => setCreating(false)} onCreated={(p) => { setCreating(false); setBox("sent"); open(p.id); loadList(); }} />}
        {!creating && !detail && <div className="p-6"><ErrorBanner error={error} /><Empty>Pick a ping, or ask another department a question.<br />You don't need to know who to ask: anyone qualified there can pick it up.</Empty></div>}
        {!creating && detail && me && meta && <Thread key={detail.id} ping={detail} reload={() => { loadDetail(); loadList(); }} />}
      </section>
    </div>
  );
}

function NewPing({ onClose, onCreated }: { onClose: () => void; onCreated: (p: Ping) => void }) {
  const { me, meta } = useAuth();
  const labels = allowedLabels(meta!.classifications, me!.clearance);
  const [to, setTo] = useState(meta!.departments.find((d) => d !== me!.department) ?? meta!.departments[0]);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [level, setLevel] = useState(labels.includes("internal") ? "internal" : labels[0]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => { e.preventDefault(); setBusy(true); setError(null);
    try { onCreated(await api<Ping>("/pings", { body: { to_department: to, title, body, min_role: level } })); }
    catch (err) { setError(err instanceof ApiError ? err.detail : "Could not send."); } finally { setBusy(false); } };
  return (
    <form onSubmit={submit} className="mx-auto max-w-2xl space-y-4 p-4 md:p-6" aria-label="New ping">
      <h2 className="text-lg font-semibold">Ask a department</h2>
      <ErrorBanner error={error} />
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Department"><select className="input" value={to} onChange={(e) => setTo(e.target.value)}>{meta!.departments.map((d) => <option key={d}>{d}</option>)}</select></Field>
        <Field label="Who may see this" hint="Only people at this level or above, in that department, can read and answer.">
          <select className="input" value={level} onChange={(e) => setLevel(e.target.value)}>{labels.map((l) => <option key={l} value={l}>{l} (level {meta!.classifications[l]})</option>)}</select>
        </Field>
      </div>
      <Field label="Subject"><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={200} placeholder="e.g. Why does checkout return a 500?" /></Field>
      <Field label="Details"><textarea className="input min-h-[140px]" value={body} onChange={(e) => setBody(e.target.value)} required maxLength={4000} placeholder="What happened, what you've tried, who is affected…" /></Field>
      <div className="flex gap-2"><button className="btn btn-primary" disabled={busy}>{busy ? "Sending…" : "Send ping"}</button><button type="button" className="btn" onClick={onClose}>Cancel</button></div>
    </form>
  );
}

function Thread({ ping, reload }: { ping: Ping; reload: () => void }) {
  const { me, meta } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [kind, setKind] = useState<"answer" | "comment">(ping.can.answer ? "answer" : "comment");
  const labels = allowedLabels(meta!.classifications, me!.clearance);
  const [level, setLevel] = useState(labels.includes(ping.min_role) ? ping.min_role : labels[0]);
  const act = async (fn: () => Promise<unknown>) => { setError(null); try { await fn(); reload(); } catch (e) { setError(e instanceof ApiError ? e.detail : "Something went wrong."); } };
  const post = (e: FormEvent) => { e.preventDefault(); act(async () => { await api(`/pings/${ping.id}/messages`, { body: { kind, body: text, min_role: level } }); setText(""); }); };
  const higher = (meta!.classifications[level] ?? 0) > (meta!.classifications[ping.min_role] ?? 0);
  const canSpeak = ping.can.answer || ping.can.comment;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 p-4 md:p-6">
      <header>
        <div className="flex flex-wrap items-center gap-2"><h2 className="text-xl font-bold">{ping.title}</h2><Badge tone={TONE[ping.status]}>{ping.status}</Badge><Badge title="Minimum level needed to see this ping">{ping.min_role}</Badge></div>
        <p className="mt-1 text-sm text-mute">{displayName(ping.asker)} → <b className="capitalize">{ping.to_department}</b> · asked {timeAgo(ping.created_at)}{ping.claimed_by && <> · picked up by <b>{displayName(ping.claimed_by)}</b></>}</p>
      </header>
      <ErrorBanner error={error} />
      <div className="flex flex-wrap gap-2">
        {ping.can.claim && <button className="btn btn-primary" onClick={() => act(() => api(`/pings/${ping.id}/claim`, { method: "POST" }))}>Pick this up</button>}
        {ping.can.release && <button className="btn" onClick={() => act(() => api(`/pings/${ping.id}/release`, { method: "POST" }))}>Put back in the queue</button>}
        {ping.can.resolve && <button className="btn btn-primary" onClick={() => act(() => api(`/pings/${ping.id}/resolve`, { method: "POST" }))}>Mark resolved</button>}
        {ping.can.close && <button className="btn btn-danger" onClick={() => act(() => api(`/pings/${ping.id}/close`, { method: "POST" }))}>Withdraw</button>}
      </div>

      <ol className="space-y-3" aria-label="Messages">
        {ping.messages?.map((m) => <Message key={m.id} m={m} mine={m.author_id === me!.id} />)}
      </ol>

      {canSpeak && (
        <form onSubmit={post} className="card space-y-3 p-3" aria-label="Reply">
          <div className="flex flex-wrap gap-3">
            {ping.can.answer && ping.can.comment && (
              <Field label="Type"><select className="input" value={kind} onChange={(e) => setKind(e.target.value as "answer" | "comment")}><option value="answer">Answer</option><option value="comment">Comment</option></select></Field>
            )}
            <Field label="Visible to"><select className="input" value={level} onChange={(e) => setLevel(e.target.value)}>{labels.map((l) => <option key={l} value={l}>{l} (level {meta!.classifications[l]})</option>)}</select></Field>
          </div>
          {higher && <Notice>People below level {meta!.classifications[level]} will see <b>[REDACTED]</b> instead of this message. Post a second, cleared version if the asker needs an answer.</Notice>}
          <textarea className="input min-h-[96px]" value={text} onChange={(e) => setText(e.target.value)} placeholder={kind === "answer" ? "Write your answer…" : "Add a comment…"} required maxLength={4000} aria-label="Reply text" />
          <button className="btn btn-primary" disabled={!text.trim()}>{kind === "answer" ? "Post answer" : "Post comment"}</button>
        </form>
      )}
      {!canSpeak && ["resolved", "closed"].includes(ping.status) && <Notice tone="brand">This ping is {ping.status}.</Notice>}

      {(ping.can.draft || ping.draft) && <DraftPanel ping={ping} reload={reload} />}
    </div>
  );
}

function Message({ m, mine }: { m: PingMessage; mine: boolean }) {
  const tone = m.kind === "answer" ? "border-good/40" : m.kind === "question" ? "border-brand/40" : "border-line";
  return (
    <li className={`flex gap-3 rounded-xl border bg-panel p-3 ${tone}`}>
      <Avatar name={m.author} />
      <div className="min-w-0 flex-1">
      <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-mute">
        <b className="text-sm text-ink">{displayName(m.author)}</b>{mine && <span>(you)</span>}<Badge tone={m.kind === "answer" ? "good" : m.kind === "question" ? "brand" : "neutral"}>{m.kind}</Badge>
        {m.min_role && <span title="Who may read this">🔒 {m.min_role}</span>}<span>{fmtTime(m.created_at)}</span>
      </div>
      {m.redacted ? <Redacted text={m.body} /> : <p className="whitespace-pre-wrap break-words text-sm">{m.body}</p>}
      </div>
    </li>
  );
}

function DraftPanel({ ping, reload }: { ping: Ping; reload: () => void }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const run = async (fn: () => Promise<void>) => { setBusy(true); setError(null); try { await fn(); } catch (e) { setError(e instanceof ApiError ? e.detail : "Something went wrong."); } finally { setBusy(false); } };
  const state = ping.draft?.state;
  return (
    <section className="card space-y-3 p-4" aria-label="Turn this into knowledge">
      <div className="flex items-center justify-between gap-2"><h3 className="flex items-center gap-1.5 font-semibold"><Sparkles size={16} className="text-brand" /> Turn this into knowledge</h3>{state && <Badge tone={state === "live" ? "good" : state === "rejected" ? "bad" : "warn"}>draft {state}</Badge>}</div>
      <p className="text-sm text-mute">Write up the solution so the next person doesn't need to ask. A draft is written for you to edit, then <b>a different person must approve it</b> before it becomes searchable knowledge.</p>
      <ErrorBanner error={error} />
      {done && <Notice tone="good">{done}</Notice>}
      {ping.can.draft && text === null && !["pending", "live"].includes(state ?? "") && (
        <button className="btn" disabled={busy} onClick={() => run(async () => { setText((await api<{ text: string }>(`/pings/${ping.id}/draft-preview`)).text); })}>{busy ? "Drafting…" : "Draft an article"}</button>
      )}
      {text !== null && (
        <>
          <textarea className="input min-h-[240px] font-mono text-xs" value={text} onChange={(e) => setText(e.target.value)} aria-label="Draft article" />
          <p className="text-xs text-mute">Personal data (like ID or card numbers) is masked automatically. Check that nothing else sensitive remains.</p>
          <div className="flex gap-2">
            <button className="btn btn-primary" disabled={busy || !text.trim()} onClick={() => run(async () => { await api(`/pings/${ping.id}/draft`, { body: { text } }); setText(null); setDone("Submitted. A colleague will review it before it goes live."); reload(); })}>Submit for review</button>
            <button className="btn" onClick={() => setText(null)}>Discard</button>
          </div>
        </>
      )}
    </section>
  );
}
