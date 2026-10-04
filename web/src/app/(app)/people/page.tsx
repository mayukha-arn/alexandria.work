"use client";
import { useEffect, useState } from "react";
import { api, type Person } from "@/lib/api";
import { displayName } from "@/lib/people";
import { Avatar } from "@/components/avatar";
import { PersonAvailability } from "@/components/person-availability";
import { Empty, ErrorBanner } from "@/components/ui";

export default function PeoplePage() {
  const [people, setPeople] = useState<Person[] | null>(null);
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    const load = () => { api<Person[]>("/directory").then((p) => { if (active) { setPeople(p); setError(null); } }).catch((e) => { if (active) setError(e.message); }); };
    load(); const timer = setInterval(load, 60_000);
    return () => { active = false; clearInterval(timer); };
  }, [attempt]);
  const filtered = (people ?? []).filter((p) => `${displayName(p.username)} ${p.username} ${p.department} ${p.role}`.toLowerCase().includes(q.trim().toLowerCase()));
  const departments = Array.from(new Set(filtered.map((p) => p.department))).sort();
  return <div className="mx-auto max-w-6xl space-y-6 p-4 md:p-8">
    <header><h1 className="text-2xl font-semibold">People</h1><p className="mt-1 text-sm text-mute">Find your colleagues across departments and time zones. Working hours are Monday–Friday, 9am–6pm local time.</p></header>
    <input className="input max-w-xl" aria-label="Search people" placeholder="Search by name, department, or role…" value={q} onChange={(e) => setQ(e.target.value)} />
    <ErrorBanner error={error} />
    {error && <button className="btn" onClick={() => setAttempt((v) => v + 1)}>Retry directory</button>}
    {!people && !error && <p role="status" className="text-sm text-mute">Loading people…</p>}
    {people && !filtered.length && <Empty>{q ? "No people match your search." : "No colleagues are listed yet."}</Empty>}
    {departments.map((dept) => <section key={dept} aria-label={`${dept} people`}>
      <h2 className="mb-3 font-semibold capitalize">{dept} <span className="ml-1 text-sm font-normal text-mute">{filtered.filter((p) => p.department === dept).length}</span></h2>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{filtered.filter((p) => p.department === dept).sort((a, b) => a.username.localeCompare(b.username)).map((p) => <article key={p.id} className="rounded-xl border border-line bg-white p-4">
        <div className="flex items-center gap-3"><Avatar name={p.username} presence={p.online ? "online" : null} /><div className="min-w-0"><h3 className="font-semibold">{displayName(p.username)}</h3><p className="text-xs text-mute">{p.role}</p></div></div>
        <PersonAvailability person={p} />
      </article>)}</div>
    </section>)}
  </div>;
}
