"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, ApiError, type Pending } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { signerFor } from "@/lib/wallet";
import { diffStats, parseDiff } from "@/lib/diff";
import { Badge, Empty, ErrorBanner, Notice, Page } from "@/components/ui";

export default function ReviewPage() {
  const { me } = useAuth();
  const [items, setItems] = useState<Pending[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const load = useCallback(() => { api<Pending[]>("/documents/pending").then((p) => { setItems(p); setLoaded(true); }).catch((e) => setError(e.message)); }, []);
  useEffect(load, [load]);

  return (
    <Page title="Review queue" subtitle="Nothing becomes company knowledge until someone other than its author approves it. Your decision is signed with your wallet and recorded on the blockchain.">
      {!me?.wallet_pubkey && <Notice>You need a linked Solana wallet to sign decisions. <Link className="underline" href="/security/">Link one on the Security page</Link>.</Notice>}
      <ErrorBanner error={error} />
      {flash && <Notice tone="good">{flash}</Notice>}
      {loaded && items.length === 0 && <Empty>Nothing waiting for your review. 🎉</Empty>}
      <div className="space-y-4">{items.map((it) => <Item key={it.id} item={it} onDone={(msg) => { setFlash(msg); load(); }} />)}</div>
    </Page>
  );
}

function Item({ item, onDone }: { item: Pending; onDone: (msg: string) => void }) {
  const { me } = useAuth();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rows = parseDiff(item.diff);
  const stats = diffStats(rows);

  const decide = async (approve: boolean) => {
    setError(null); setBusy(approve ? "approve" : "reject");
    try {
      const signer = await signerFor(me?.wallet_pubkey ?? null);
      const ch = await api<{ nonce: string; message: string }>(`/documents/pending/${item.id}/challenge`, { body: { action: approve ? "approve" : "reject" } });
      const signature = await signer.sign(ch.message);
      await api(`/documents/pending/${item.id}/decision`, { body: { approve, note: note || null, nonce: ch.nonce, signature } });
      onDone(approve ? `Approved "${item.source}". It is now searchable, and the approval is queued for the blockchain.` : `Rejected "${item.source}".`);
    } catch (e) { setError(e instanceof ApiError ? e.detail : (e as Error).message); } finally { setBusy(null); }
  };

  return (
    <article className="card space-y-3 p-4" aria-label={`Review ${item.source}`}>
      <header className="flex flex-wrap items-center gap-2">
        <h2 className="font-semibold">{item.source}</h2>
        <Badge tone={item.is_new_document ? "brand" : "neutral"}>{item.is_new_document ? "new document" : `update · ${Math.round(item.similarity * 100)}% similar`}</Badge>
        <Badge>{item.min_role}</Badge>{item.department && <Badge>{item.department}</Badge>}
        <Badge tone={item.risk_level === "high" ? "bad" : "good"}>{item.risk_level === "high" ? "⚠ high risk" : "normal risk"}</Badge>
        <span className="ml-auto text-xs text-mute">submitted by <b>{(item as Pending & { uploader?: string }).uploader ?? "unknown"}</b></span>
      </header>
      {item.risk_reasons.length > 0 && (
        <ul className="space-y-1 rounded-lg border border-bad/40 bg-bad/10 p-3 text-sm" aria-label="Risk flags">
          {item.risk_reasons.map((r, i) => <li key={i} className={r.startsWith("possible poisoning") ? "font-semibold text-bad" : "text-warn"}>{r.startsWith("possible poisoning") ? "🚨 " : "⚠ "}{r}</li>)}
        </ul>
      )}
      <div className="text-xs text-mute">{stats.added} line(s) added · {stats.removed} removed</div>
      <div className="max-h-96 overflow-auto rounded-lg border border-line font-mono text-xs" role="region" aria-label="Changes">
        <div className="grid grid-cols-2 border-b border-line bg-panel2 text-mute"><div className="px-2 py-1">{item.is_new_document ? "—" : "Current (live)"}</div><div className="px-2 py-1">Proposed</div></div>
        {rows.map((r, i) => (
          <div key={i} className="grid grid-cols-2">
            <div className={`whitespace-pre-wrap break-words px-2 py-0.5 ${r.kind === "removed" || r.kind === "changed" ? "bg-bad/15" : ""}`}>{r.left ?? ""}</div>
            <div className={`whitespace-pre-wrap break-words px-2 py-0.5 ${r.kind === "added" || r.kind === "changed" ? "bg-good/15" : ""}`}>{r.right ?? ""}</div>
          </div>
        ))}
      </div>
      <ErrorBanner error={error} />
      <div className="flex flex-wrap items-end gap-2">
        <input className="input max-w-md flex-1" placeholder="Optional note (kept with the decision)" value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} aria-label="Note" />
        <button className="btn btn-primary" disabled={busy !== null} onClick={() => decide(true)}>{busy === "approve" ? "Signing…" : "Approve & sign"}</button>
        <button className="btn btn-danger" disabled={busy !== null} onClick={() => decide(false)}>{busy === "reject" ? "Signing…" : "Reject & sign"}</button>
      </div>
    </article>
  );
}
