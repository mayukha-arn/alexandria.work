"use client";
import { useCallback, useEffect, useState } from "react";
import { api, type Insights } from "@/lib/api";
import { useLive } from "@/lib/realtime";

export function InsightsStrip() {
  const [stats, setStats] = useState<Insights | null>(null);
  const [error, setError] = useState(false);
  const load = useCallback(() => { api<Insights>("/insights").then((s) => { setStats(s); setError(false); }).catch(() => setError(true)); }, []);
  useEffect(() => { load(); const timer = setInterval(load, 60_000); return () => clearInterval(timer); }, [load]);
  useLive((e) => { if (e.type.startsWith("ping.") || e.type.startsWith("document.")) load(); });
  if (error) return <div className="mb-6 text-xs text-mute">Knowledge activity is unavailable. <button className="font-semibold text-brand" onClick={load}>Retry</button></div>;
  const items = [
    ["Documents you can read", stats?.documents], ["Learned from requests", stats?.learned],
    ["Requests resolved", stats?.resolved], ["Requests waiting", stats?.waiting],
    ["Median first answer", stats ? stats.median_first_answer_min === null ? "—" : stats.median_first_answer_min === 0 ? "<1 min" : `${stats.median_first_answer_min} min` : undefined],
  ];
  return <section aria-label="Knowledge activity" className="mb-6 rounded-xl border border-line bg-white p-4"><dl className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">{items.map(([label, value]) => <div key={label}><dt className="text-xs text-mute">{label}</dt><dd className="mt-1 text-lg font-semibold">{value ?? "…"}</dd></div>)}</dl></section>;
}
