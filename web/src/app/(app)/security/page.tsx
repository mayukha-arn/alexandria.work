"use client";
import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type SessionInfo } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { connectPhantom, demoSigner, hasDemoWallet, phantomAvailable, shortKey, type Signer } from "@/lib/wallet";
import { fmtTime } from "@/lib/format";
import { Badge, ErrorBanner, Notice, Page } from "@/components/ui";

export default function SecurityPage() {
  const { me, reloadMe } = useAuth();
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const loadSessions = useCallback(() => { api<SessionInfo[]>("/auth/sessions").then(setSessions).catch((e) => setError(e.message)); }, []);
  useEffect(loadSessions, [loadSessions]);

  const link = async (get: () => Promise<Signer>) => {
    setBusy(true); setError(null); setFlash(null);
    try {
      const signer = await get();
      const ch = await api<{ nonce: string; message: string }>("/wallet/challenge", { method: "POST" });
      await api("/wallet/link", { body: { pubkey: signer.publicKey, nonce: ch.nonce, signature: await signer.sign(ch.message) } });
      await reloadMe();
      setFlash("Wallet linked. You can now sign document reviews.");
    } catch (e) { setError(e instanceof ApiError ? e.detail : (e as Error).message); } finally { setBusy(false); }
  };
  const revoke = async (jti: string) => { try { await api(`/auth/sessions/${jti}`, { method: "DELETE" }); loadSessions(); } catch (e) { setError(e instanceof ApiError ? e.detail : "Could not sign out that session."); } };

  return (
    <Page title="Security" subtitle="Your sign-in protection and your signing wallet.">
      <ErrorBanner error={error} />
      {flash && <Notice tone="good">{flash}</Notice>}

      <section className="card space-y-2 p-4"><h2 className="font-semibold">Two-factor authentication</h2>
        <p className="text-sm"><Badge tone="good">On</Badge> <span className="ml-2 text-mute">Required for every account. You'll be asked for a code each time you sign in.</span></p></section>

      <section className="card space-y-3 p-4" aria-label="Signing wallet">
        <h2 className="font-semibold">Signing wallet</h2>
        {me?.wallet_pubkey ? (
          <>
            <p className="text-sm">Linked: <code className="break-all font-mono text-xs" data-testid="wallet-key">{me.wallet_pubkey}</code> {hasDemoWallet() && demoSigner(false)?.publicKey === me.wallet_pubkey && <Badge tone="brand">browser wallet</Badge>}</p>
            <p className="text-sm text-mute">Your approvals and rejections of documents are signed with this wallet, so who approved what can be proven. To change it, ask your manager or an admin to reset it.</p>
          </>
        ) : (
          <>
            <p className="text-sm text-mute">Link a Solana wallet to sign document reviews. You prove you control it by signing a one-time message; no funds or transactions are involved.</p>
            <div className="flex flex-wrap gap-2">
              <button className="btn btn-primary" disabled={busy || !phantomAvailable()} onClick={() => link(connectPhantom)} title={phantomAvailable() ? "" : "Phantom wasn't detected in this browser"}>Connect Phantom</button>
              <button className="btn" disabled={busy} onClick={() => link(async () => demoSigner(true)!)}>Create a wallet in this browser</button>
            </div>
            {!phantomAvailable() && <p className="text-xs text-mute">No Solana wallet extension detected. Install Phantom for real use.</p>}
            <Notice>A browser wallet keeps its key in this browser's storage. For high-value approvals, link Phantom or a hardware wallet instead.</Notice>
          </>
        )}
      </section>

      <section className="card space-y-3 p-4" aria-label="Active sessions">
        <h2 className="font-semibold">Where you're signed in</h2>
        <ul className="divide-y divide-line">
          {sessions.map((s) => (
            <li key={s.jti} className="flex flex-wrap items-center gap-2 py-2 text-sm">
              <span>{s.ip ?? "unknown address"}</span><span className="min-w-0 max-w-xs truncate text-xs text-mute">{s.user_agent}</span><span className="text-xs text-mute">since {fmtTime(s.created_at)}</span>
              {s.current ? <Badge tone="good">this device</Badge> : <button className="btn btn-danger ml-auto" onClick={() => revoke(s.jti)}>Sign out</button>}
            </li>
          ))}
        </ul>
      </section>
    </Page>
  );
}
