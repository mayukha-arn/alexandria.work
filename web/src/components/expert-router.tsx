"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ArrowRight, Users } from "lucide-react";
import { api, type Ping, type Route } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { allowedLabels, cap } from "@/lib/format";
import { displayName } from "@/lib/people";
import { Avatar } from "./avatar";
import { ErrorBanner, Field } from "./ui";
import { PersonAvailability } from "./person-availability";

export function ExpertRouter({ question }: { question: string }) {
  const { me, meta } = useAuth();
  const labels = allowedLabels(meta?.classifications ?? {}, me?.clearance ?? 0);
  const [level, setLevel] = useState(labels.includes("internal") ? "internal" : labels[0] ?? "public");
  const [route, setRoute] = useState<Route | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  const locked = useRef(false);
  const [sent, setSent] = useState<Ping | null>(null);
  const [department, setDepartment] = useState("");

  useEffect(() => {
    let active = true;
    setRoute(null); setError(null);
    api<Route>("/route", { body: { question } }).then((r) => { if (active) setRoute(r); })
      .catch((e) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [question, attempt]);

  const send = async (target: { to_department?: string; to_user?: string }) => {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError(null);
    try {
      setSent(await api<Ping>("/pings", { body: { ...target, title: question.slice(0, 200), body: question, min_role: level } }));
    } catch (e) { setError(e instanceof Error ? e.message : "Could not send your request."); locked.current = false; }
    finally { setBusy(false); }
  };

  if (sent) return <div className="rounded-xl border border-good/25 bg-good/5 p-4 text-sm" role="status">
    <p className="font-semibold">Request sent to {sent.requested ? displayName(sent.requested) : cap(sent.to_department)}.</p>
    <p className="mt-1 text-mute">Their qualified teammates can also help. Continue the conversation in Requests.</p>
    <Link className="mt-3 inline-flex items-center gap-1 font-semibold text-brand" href={`/pings/?id=${sent.id}`}>Open conversation <ArrowRight size={14} /></Link>
  </div>;

  return <section className="space-y-4 rounded-2xl border border-line bg-panel2/50 p-4" aria-label="Find someone to help">
    <div><h3 className="font-semibold">Find someone to help</h3><p className="mt-1 text-sm text-mute">Send your question to a department queue or an expert with relevant experience.</p></div>
    <ErrorBanner error={error} />
    {!route && !error && <p role="status" className="text-sm text-mute">Finding departments and experts…</p>}
    {!route && error && <button className="btn" onClick={() => setAttempt((v) => v + 1)}>Retry recommendations</button>}
    <Field label="Who may see this request" hint="Your question will be shared with qualified people in the chosen department at this level or above.">
      <select className="input" value={level} onChange={(e) => setLevel(e.target.value)} disabled={busy}>{labels.map((l) => <option key={l} value={l}>{cap(l)} (level {meta?.classifications[l]})</option>)}</select>
    </Field>
    {!!route?.departments.length && <div className="grid gap-3 sm:grid-cols-2">
      {route.departments.map((d) => <article className="rounded-xl border border-line bg-white p-3" key={d.department}>
        <h4 className="flex items-center gap-2 font-semibold capitalize"><Users size={16} className="text-brand" />{d.department}</h4>
        <p className="mt-1 text-xs text-mute">{d.working_now} available now · {d.members} qualified {d.members === 1 ? "teammate" : "teammates"}</p>
        <button className="btn mt-3 w-full" disabled={busy || !d.members} onClick={() => send({ to_department: d.department })}>Send to queue (follow-the-sun)</button>
      </article>)}
    </div>}
    {!!route?.experts.length && <div className="grid gap-3 sm:grid-cols-2">
      {route.experts.map((p) => <article className="flex flex-col rounded-xl border border-line bg-white p-3" key={p.id}>
        <div className="flex items-center gap-3"><Avatar name={p.username} presence={p.online ? "online" : null} /><div className="min-w-0"><h4 className="font-semibold">{displayName(p.username)}</h4><p className="text-xs capitalize text-mute">{p.role} · {p.department}</p></div></div>
        <PersonAvailability person={p} />
        <ul className="mb-3 mt-2 space-y-1 text-xs text-mute">{(p.reasons.length ? p.reasons : ["Member of a relevant department"]).map((reason) => <li key={reason}>{reason}</li>)}</ul>
        <button className="btn mt-auto w-full" disabled={busy} onClick={() => send({ to_user: p.username })}>Ask {displayName(p.username)}</button>
      </article>)}
    </div>}
    {route && !route.departments.length && !route.experts.length && <p className="text-sm text-mute">No clear match yet. Choose a department to start a request.</p>}
    <div className="flex flex-wrap items-end gap-2 border-t border-line pt-3">
      <div className="min-w-0 flex-1"><Field label="Choose a department"><select className="input" value={department} disabled={busy} onChange={(e) => setDepartment(e.target.value)}><option value="">Select department…</option>{meta?.departments.map((d) => <option key={d} value={d}>{cap(d)}</option>)}</select></Field></div>
      <button className="btn" disabled={busy || !department} onClick={() => send({ to_department: department })}>{busy ? "Sending…" : "Send request"}</button>
    </div>
  </section>;
}
