"use client";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ExternalLink } from "lucide-react";
import { API, api, ApiError, type LedgerRow } from "@/lib/api";
import { explorerUrl, fmtTime } from "@/lib/format";
import { Badge, Empty, ErrorBanner, Notice, Page, Redacted } from "@/components/ui";

type Status = { chain_configured: boolean; total: number; anchored: number; queued: number; rejected: number };

export default function AuditPage() {
  const [rows, setRows] = useState<LedgerRow[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hash, setHash] = useState("");
  const [verdict, setVerdict] = useState<{ tone: "good" | "warn" | "bad" | "brand"; text: string } | null>(null);

  const load = useCallback(() => {
    Promise.all([api<LedgerRow[]>("/audit/ledger?limit=100"), api<Status>("/audit/status")]).then(([r, s]) => { setRows(r); setStatus(s); }).catch((e) => setError(e.message));
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);

  const verify = async (e: FormEvent) => {
    e.preventDefault(); setVerdict(null);
    try {
      const r = await api<{ status: string; chain?: { approver: string | null } }>(`/audit/verify/${hash.trim()}`);
      setVerdict({
        verified: { tone: "good" as const, text: `Verified: the text is unchanged and its approval is recorded on-chain${r.chain?.approver ? ` (approver wallet ${r.chain.approver.slice(0, 6)}…)` : ""}.` },
        unanchored: { tone: "warn" as const, text: "The text is intact, but no on-chain approval was found (it may still be queued)." },
        tampered: { tone: "bad" as const, text: "TAMPERED: the stored text no longer matches the hash that was approved." },
        not_found: { tone: "warn" as const, text: "No such document." },
      }[r.status] ?? { tone: "brand", text: r.status });
    } catch (err) { setVerdict({ tone: "warn", text: err instanceof ApiError ? (err.status === 404 ? "No such document (or you are not cleared to see it)." : err.detail) : "Could not verify." }); }
  };

  return (
    <Page title="Audit ledger" subtitle="Every approval, access change and sensitive action is recorded as a fingerprint on Solana. Details above your clearance are hidden from you.">
      <ErrorBanner error={error} />
      {status && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {[["Recorded", status.total, "neutral"], ["On the blockchain", status.anchored, "good"], ["Waiting to be sent", status.queued, status.queued ? "warn" : "neutral"], ["Refused", status.rejected, status.rejected ? "bad" : "neutral"]].map(([l, n, t]) => (
            <div key={l as string} className="card p-3"><div className="text-xs uppercase tracking-wide text-mute">{l}</div><div className="mt-1 text-2xl font-semibold">{n as number}</div></div>
          ))}
        </div>
      )}
      {status && !status.chain_configured && <Notice>The blockchain connection is switched off, so events are queued here and will be sent once it is enabled.</Notice>}

      <form onSubmit={verify} className="card flex flex-wrap items-end gap-2 p-3" aria-label="Verify a document">
        <label className="min-w-[16rem] flex-1"><span className="label">Check a document's integrity</span><input className="input font-mono text-xs" value={hash} onChange={(e) => setHash(e.target.value)} placeholder="document hash (64 hex characters)" /></label>
        <button className="btn" disabled={hash.trim().length < 8}>Verify</button>
      </form>
      {verdict && <Notice tone={verdict.tone === "bad" || verdict.tone === "warn" ? "warn" : verdict.tone === "good" ? "good" : "brand"}>{verdict.text}</Notice>}

      {rows.length === 0 ? <Empty>No events yet.</Empty> : (
        <div className="card overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-line text-xs uppercase tracking-wide text-mute"><tr><th className="p-3">When</th><th>Dept</th><th>Actor</th><th>Action</th><th>Details</th><th>Needs level</th><th>Blockchain</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-b border-line/60 align-top last:border-0">
                  <td className="p-3 whitespace-nowrap text-xs">{fmtTime(r.created_at)}</td>
                  <td>{r.department ?? "—"}</td>
                  <td className="font-mono text-xs" title="A keyed fingerprint of who did it: identical for the same person, meaningless without the server key">{r.actor_hash ? r.actor_hash.slice(0, 10) + "…" : "—"}</td>
                  <td>{r.redacted ? <Redacted text={r.action} /> : <Badge>{r.action}</Badge>}</td>
                  <td className="max-w-xs">{r.redacted ? <Redacted text={String(r.payload)} /> : <details><summary className="cursor-pointer truncate font-mono text-xs text-mute">{JSON.stringify(r.payload)}</summary><pre className="mt-1 whitespace-pre-wrap break-all font-mono text-xs">{JSON.stringify(r.payload, null, 2)}</pre></details>}</td>
                  <td><Badge tone={r.redacted ? "bad" : "neutral"}>{r.required_clearance}</Badge></td>
                  <td className="whitespace-nowrap">{r.tx_signature ? <a className="inline-flex items-center gap-1 text-xs text-brand underline" href={explorerUrl(r.tx_signature, API)} target="_blank" rel="noreferrer">{r.tx_signature.slice(0, 8)}… <ExternalLink size={12} /></a> : <Badge tone={r.anchored ? "good" : "warn"}>{r.anchored ? "recorded" : "queued"}</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Page>
  );
}
