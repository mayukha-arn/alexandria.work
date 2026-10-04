"use client";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiError, has, type Doc } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { allowedLabels, cap } from "@/lib/format";
import { Badge, Empty, ErrorBanner, Field, Notice, Page } from "@/components/ui";

const VERIFY_TONE: Record<string, "good" | "warn" | "bad"> = { verified: "good", unanchored: "warn", tampered: "bad" };

export default function DocumentsPage() {
  const { me } = useAuth();
  const [docs, setDocs] = useState<Doc[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [verdicts, setVerdicts] = useState<Record<string, string>>({});
  const [confirm, setConfirm] = useState<string | null>(null);
  const canPurge = has(me, "purge_document");

  const load = useCallback(() => { api<Doc[]>("/documents").then(setDocs).catch((e) => setError(e.message)); }, []);
  useEffect(load, [load]);

  const verify = async (hash: string) => {
    try { const r = await api<{ status: string }>(`/audit/verify/${hash}`); setVerdicts((v) => ({ ...v, [hash]: r.status })); }
    catch (e) { setVerdicts((v) => ({ ...v, [hash]: e instanceof ApiError && e.status === 503 ? "unavailable" : "error" })); }
  };
  const purge = async (hash: string) => { setConfirm(null); try { await api(`/documents/${hash}`, { method: "DELETE" }); load(); } catch (e) { setError(e instanceof ApiError ? e.detail : "Could not delete."); } };

  return (
    <Page title="Knowledge base" subtitle="Documents you are cleared to read. Every document was approved by someone other than whoever submitted it.">
      {has(me, "upload_doc") && <Upload onDone={load} />}
      <ErrorBanner error={error} />
      {docs.length === 0 ? <Empty>No approved documents yet. Upload one: a colleague will review it before it goes live.</Empty> : (
        <div className="card overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-line text-xs uppercase tracking-wide text-mute"><tr><th className="p-3">Document</th><th>Department</th><th>Visible to</th><th>Status</th><th>Integrity</th><th /></tr></thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.doc_hash} className="border-b border-line/60 last:border-0">
                  <td className="max-w-xs p-3"><div className="truncate font-medium" title={d.source}>{d.source}</div><div className="font-mono text-xs text-mute">{d.doc_hash.slice(0, 12)}…</div></td>
                  <td>{d.department ?? "—"}</td>
                  <td><Badge>{d.min_role}</Badge></td>
                  <td><Badge tone={d.status === "active" ? "good" : "neutral"}>{d.status === "active" ? "live" : d.status}</Badge>{!d.indexed && d.status === "active" && <Badge tone="warn" title="Still being indexed for search">indexing</Badge>}</td>
                  <td>{verdicts[d.doc_hash] ? <Badge tone={VERIFY_TONE[verdicts[d.doc_hash]] ?? "neutral"}>{verdicts[d.doc_hash]}</Badge> : <button className="btn" onClick={() => verify(d.doc_hash)}>Verify</button>}</td>
                  <td className="p-3 text-right">
                    {canPurge && (confirm === d.doc_hash
                      ? <span className="space-x-1"><button className="btn btn-danger" onClick={() => purge(d.doc_hash)}>Confirm delete</button><button className="btn" onClick={() => setConfirm(null)}>Cancel</button></span>
                      : <button className="btn btn-danger" onClick={() => setConfirm(d.doc_hash)} title="Right to be forgotten: removes the document and its search data">Delete</button>)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Page>
  );
}

function Upload({ onDone }: { onDone: () => void }) {
  const { me, meta } = useAuth();
  const labels = allowedLabels(meta!.classifications, me!.clearance);
  const [mode, setMode] = useState<"file" | "url">("file");
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const [level, setLevel] = useState(labels.includes("internal") ? "internal" : labels[0]);
  const [dept, setDept] = useState(me!.department);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ tone: "good" | "warn" | "brand"; text: string } | null>(null);
  const admin = me!.level === "admin";

  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setError(null); setResult(null);
    const form = new FormData();
    if (mode === "file" && file) form.append("file", file); else form.append("url", url);
    form.append("min_role", level); form.append("department", dept);
    try {
      const r = await api<{ status: string; risk_level?: string; similarity?: number }>("/documents", { form });
      const text = ({
        pending_approval: `Submitted for review${r.risk_level === "high" ? " (flagged high-risk: reviewers will look closely)" : ""}. A colleague must approve it before anyone can search it.`,
        duplicate_skipped: "That exact document is already in the system, so nothing was added.",
        near_duplicate_discarded: "That is almost identical to an existing document, so it was discarded.",
        new_document_ingested: "Added.", version_update_ingested: "Updated.",
      } as Record<string, string>)[r.status] ?? r.status;
      setResult({ tone: r.status === "pending_approval" ? "brand" : r.status.includes("ingested") ? "good" : "warn", text });
      setFile(null); setUrl(""); onDone();
    } catch (err) { setError(err instanceof ApiError ? err.detail : "Upload failed."); } finally { setBusy(false); }
  };

  return (
    <form onSubmit={submit} className="card space-y-3 p-4" aria-label="Add a document">
      <div className="flex items-center justify-between"><h2 className="font-semibold">Add a document</h2>
        <div role="tablist" className="flex gap-1">{(["file", "url"] as const).map((m) => <button type="button" key={m} role="tab" aria-selected={mode === m} onClick={() => setMode(m)} className={`rounded-lg px-3 py-1 text-sm ${mode === m ? "bg-brand/15" : "text-mute"}`}>{m === "file" ? "PDF file" : "Web page"}</button>)}</div></div>
      <ErrorBanner error={error} />
      {result && <Notice tone={result.tone}>{result.text}</Notice>}
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="sm:col-span-3">
          {mode === "file"
            ? <Field label="PDF" hint="Text PDFs only; scanned images are skipped."><input className="input" type="file" accept="application/pdf,.pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} required /></Field>
            : <Field label="Address"><input className="input" type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://docs.example.com/payroll" required /></Field>}
        </div>
        <Field label="Who may read it" hint="You can't pick a level above your own."><select className="input" value={level} onChange={(e) => setLevel(e.target.value)}>{labels.map((l) => <option key={l} value={l}>{l} (level {meta!.classifications[l]})</option>)}</select></Field>
        <Field label="Owning department"><select className="input" value={dept} disabled={!admin} onChange={(e) => setDept(e.target.value)}>{meta!.departments.map((d) => <option key={d}>{d}</option>)}</select></Field>
        <div className="flex items-end"><button className="btn btn-primary w-full" disabled={busy || (mode === "file" ? !file : !url)}>{busy ? "Uploading…" : `Submit ${cap(mode === "file" ? "document" : "page")}`}</button></div>
      </div>
    </form>
  );
}
