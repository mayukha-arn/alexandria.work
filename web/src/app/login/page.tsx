"use client";
import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import QRCode from "qrcode";
import { ShieldCheck } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ErrorBanner, Field, Notice } from "@/components/ui";

type Step = { kind: "creds" } | { kind: "mfa"; token: string } | { kind: "enroll"; token: string; uri: string; secret: string; qr: string };

export default function Login() {
  const { status, signIn } = useAuth();
  const router = useRouter();
  const [step, setStep] = useState<Step>({ kind: "creds" });
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (status === "authed") router.replace("/chat/"); }, [status, router]);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setError(null);
    try { await fn(); } catch (e) { setError(e instanceof ApiError ? e.detail : "Something went wrong."); } finally { setBusy(false); }
  };

  const submitCreds = (e: FormEvent) => { e.preventDefault(); run(async () => {
    const r = await api<{ status: string; mfa_token?: string; enroll_token?: string }>("/auth/login", { body: { username, password }, token: null });
    setPassword("");
    if (r.status === "mfa_required") { setStep({ kind: "mfa", token: r.mfa_token! }); return; }
    const t = r.enroll_token!;
    const s = await api<{ otpauth_uri: string; secret: string }>("/auth/2fa/setup", { method: "POST", token: t, quiet401: true });
    setStep({ kind: "enroll", token: t, uri: s.otpauth_uri, secret: s.secret, qr: await QRCode.toDataURL(s.otpauth_uri, { margin: 1, width: 220 }) });
  }); };

  const submitCode = (e: FormEvent) => { e.preventDefault(); if (step.kind === "creds") return; run(async () => {
    const path = step.kind === "mfa" ? "/auth/2fa/verify" : "/auth/2fa/enable";
    const r = await api<{ access_token: string }>(path, { body: { code }, token: step.token, quiet401: true });
    await signIn(r.access_token);
    router.replace("/chat/");
  }); };

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-6 p-6">
      <div className="text-center">
        <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-brand/20 text-brand"><ShieldCheck /></div>
        <h1 className="text-2xl font-semibold">Alexandria</h1>
        <p className="mt-1 text-sm text-mute">Private knowledge and messaging for your organisation</p>
      </div>

      <div className="card p-5">
        <ErrorBanner error={error} />
        {step.kind === "creds" && (
          <form onSubmit={submitCreds} className="mt-0 space-y-4" aria-label="Sign in">
            <Field label="Username"><input className="input" autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required /></Field>
            <Field label="Password"><input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></Field>
            <button className="btn btn-primary w-full" disabled={busy}>{busy ? "Checking…" : "Continue"}</button>
            <p className="text-xs text-mute">Two-factor authentication is required for every account.</p>
          </form>
        )}

        {step.kind === "enroll" && (
          <form onSubmit={submitCode} className="space-y-4" aria-label="Set up two-factor authentication">
            <Notice tone="brand">First sign-in: set up two-factor authentication. Scan the code with an authenticator app (Google Authenticator, Authy, 1Password…), then enter the 6-digit code it shows.</Notice>
            <div className="flex flex-col items-center gap-2">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={step.qr} alt="Two-factor setup QR code" width={220} height={220} className="rounded-lg bg-white p-2" />
              <details className="text-xs text-mute"><summary className="cursor-pointer">Can't scan? Enter the key by hand</summary><code data-testid="totp-secret" className="mt-1 block break-all font-mono text-ink">{step.secret}</code></details>
            </div>
            <CodeInput value={code} onChange={setCode} />
            <button className="btn btn-primary w-full" disabled={busy || code.length < 6}>{busy ? "Verifying…" : "Turn on 2FA and sign in"}</button>
          </form>
        )}

        {step.kind === "mfa" && (
          <form onSubmit={submitCode} className="space-y-4" aria-label="Two-factor code">
            <p className="text-sm text-mute">Enter the 6-digit code from your authenticator app.</p>
            <CodeInput value={code} onChange={setCode} />
            <button className="btn btn-primary w-full" disabled={busy || code.length < 6}>{busy ? "Verifying…" : "Sign in"}</button>
          </form>
        )}

        {step.kind !== "creds" && (
          <button type="button" className="mt-3 w-full text-center text-xs text-mute hover:text-ink" onClick={() => { setStep({ kind: "creds" }); setCode(""); setError(null); }}>← Use a different account</button>
        )}
      </div>
    </main>
  );
}

function CodeInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <Field label="6-digit code">
      <input className="input text-center font-mono text-xl tracking-[0.4em]" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} autoFocus
        value={value} onChange={(e) => onChange(e.target.value.replace(/\D/g, "").slice(0, 6))} aria-label="6-digit code" />
    </Field>
  );
}
