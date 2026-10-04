"use client";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiError, type ManagedUser } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cap, } from "@/lib/format";
import { shortKey } from "@/lib/wallet";
import { Badge, ErrorBanner, Field, Notice, Page } from "@/components/ui";

export default function GovernancePage() {
  const { me, meta } = useAuth();
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const load = useCallback(() => { api<ManagedUser[]>("/users").then(setUsers).catch((e) => setError(e.message)); }, []);
  useEffect(load, [load]);
  const guard = async (fn: () => Promise<unknown>, ok: string) => {
    setError(null); setFlash(null);
    try { await fn(); setFlash(ok); load(); } catch (e) { setError(e instanceof ApiError ? e.detail : "Something went wrong."); }
  };

  return (
    <Page title="Governance" subtitle="Manage access for the people who report to you. Every change is recorded in the audit ledger.">
      <Notice tone="brand">You can change clearance and rights for <b>your direct reports only</b>, never above your own level ({me?.clearance}), and only grant rights you hold yourself. Changes apply immediately, even to people who are signed in.</Notice>
      <ErrorBanner error={error} />
      {flash && <Notice tone="good">{flash}</Notice>}
      <div className="space-y-3">
        {users.map((u) => <UserCard key={u.id} u={u} isMe={u.id === me?.id} guard={guard} />)}
      </div>
      <NewUser guard={guard} roles={meta!.roles} />
    </Page>
  );
}

function UserCard({ u, isMe, guard }: { u: ManagedUser; isMe: boolean; guard: (fn: () => Promise<unknown>, ok: string) => void }) {
  const { me, meta } = useAuth();
  const [level, setLevel] = useState(u.clearance);
  useEffect(() => setLevel(u.clearance), [u.clearance]);
  const max = me!.clearance;
  const tooHigh = u.clearance > max;
  const editable = !isMe && !tooHigh;

  return (
    <article className="card space-y-3 p-4" aria-label={`Access for ${u.username}`}>
      <header className="flex flex-wrap items-center gap-2">
        <h2 className="font-semibold">{u.username}</h2>{isMe && <Badge>you</Badge>}
        <Badge tone="brand">{u.label}</Badge><Badge>{u.department}</Badge>
        <Badge tone={u.wallet_pubkey ? "good" : "warn"} title={u.wallet_pubkey ?? "No wallet linked: can't sign reviews"}>wallet {u.wallet_pubkey ? shortKey(u.wallet_pubkey) : "not linked"}</Badge>
        <Badge tone={u.totp_enrolled ? "good" : "warn"}>{u.totp_enrolled ? "2FA on" : "2FA not set up"}</Badge>
      </header>
      {isMe ? <p className="text-sm text-mute">You can't change your own access.</p> : tooHigh ? <p className="text-sm text-mute">Their clearance is above yours, so you can't change it.</p> : (
        <>
          <div>
            <label className="label" htmlFor={`cl-${u.id}`}>Clearance: {level}{level !== u.clearance && <span className="ml-2 normal-case text-warn">(unsaved)</span>}</label>
            <div className="flex items-center gap-3">
              <input id={`cl-${u.id}`} className="w-full max-w-md accent-emerald-600" type="range" min={0} max={max} step={10} value={Math.min(level, max)} onChange={(e) => setLevel(Number(e.target.value))} />
              <button className="btn" disabled={level === u.clearance} onClick={() => guard(() => api(`/users/${u.id}/clearance`, { method: "PUT", body: { level } }), `Set ${u.username}'s clearance to ${level}.`)}>Save</button>
            </div>
            <p className="mt-1 text-xs text-mute">Anything classified above this level is hidden from them: documents, answers, ledger details.</p>
          </div>
          <div>
            <span className="label">Rights</span>
            <ul className="grid gap-1 sm:grid-cols-2 md:grid-cols-3">
              {meta!.capabilities.map((c) => {
                const on = u.capabilities.includes(c);
                const canGrant = me!.capabilities.includes(c);
                return (
                  <li key={c}>
                    <label className={`flex items-center gap-2 text-sm ${!on && !canGrant ? "opacity-50" : ""}`} title={!on && !canGrant ? "You can only grant rights you hold yourself" : undefined}>
                      <input type="checkbox" checked={on} disabled={!on && !canGrant} onChange={() => guard(() => api(`/users/${u.id}/rights`, { body: { cap: c, action: on ? "revoke" : "grant" } }), `${on ? "Removed" : "Granted"} "${cap(c)}" ${on ? "from" : "to"} ${u.username}.`)} />
                      {cap(c)}
                    </label>
                  </li>
                );
              })}
            </ul>
          </div>
          {u.wallet_pubkey && <button className="btn" onClick={() => guard(() => api(`/users/${u.id}/wallet`, { method: "DELETE" }), `Reset ${u.username}'s wallet. They can link a new one.`)}>Reset wallet</button>}
        </>
      )}
    </article>
  );
}

function NewUser({ guard, roles }: { guard: (fn: () => Promise<unknown>, ok: string) => void; roles: { name: string; label: string; clearance: number; level: string }[] }) {
  const { me } = useAuth();
  const allowed = roles.filter((r) => r.clearance <= me!.clearance && (r.level !== "admin" || me!.level === "admin"));
  const [username, setUsername] = useState(""); const [password, setPassword] = useState(""); const [role, setRole] = useState(allowed[0]?.name ?? "");
  const submit = (e: FormEvent) => { e.preventDefault(); guard(async () => { await api("/users", { body: { username, password, role } }); setUsername(""); setPassword(""); }, `Created ${username}. They must set up 2FA when they first sign in.`); };
  return (
    <form onSubmit={submit} className="card space-y-3 p-4" aria-label="Add a person">
      <h2 className="font-semibold">Add someone to your team</h2>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Username"><input className="input" value={username} onChange={(e) => setUsername(e.target.value)} pattern="[A-Za-z0-9_.@\-]+" minLength={3} required /></Field>
        <Field label="Temporary password" hint="At least 12 characters. Share it privately."><input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={12} required autoComplete="new-password" /></Field>
        <Field label="Role" hint="Limited to roles at or below your own level."><select className="input" value={role} onChange={(e) => setRole(e.target.value)}>{allowed.map((r) => <option key={r.name} value={r.name}>{r.label} (level {r.clearance})</option>)}</select></Field>
      </div>
      <button className="btn btn-primary">Create account</button>
    </form>
  );
}
