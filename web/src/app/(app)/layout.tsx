"use client";
import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { BookOpen, ClipboardCheck, Hash, Inbox, LogOut, ScrollText, ShieldCheck, Sparkles, Users, KeyRound } from "lucide-react";
import { api, has, type Pending, type Ping } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { RealtimeProvider, useLive, useRealtime } from "@/lib/realtime";
import { Badge } from "@/components/ui";

export default function AppLayout({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();
  useEffect(() => { if (status === "anon") router.replace("/login/"); }, [status, router]);
  if (status !== "authed") return <p className="p-6 text-sm text-mute">Loading…</p>;
  return <RealtimeProvider><Shell>{children}</Shell></RealtimeProvider>;
}

function Shell({ children }: { children: ReactNode }) {
  const { me, signOut } = useAuth();
  const { status: rt } = useRealtime();
  const path = usePathname();
  const [inbox, setInbox] = useState(0);
  const [review, setReview] = useState(0);
  const canReview = has(me, "approve_doc");

  const load = () => {
    api<Ping[]>("/pings?box=inbox").then((p) => setInbox(p.filter((x) => x.status === "open").length)).catch(() => {});
    if (canReview) api<Pending[]>("/documents/pending").then((p) => setReview(p.length)).catch(() => {});
  };
  useEffect(load, [canReview]);  // eslint-disable-line react-hooks/exhaustive-deps
  useLive((e) => { if (e.type.startsWith("ping.")) load(); });

  const nav = [
    { href: "/chat/", label: "Chat", icon: Hash, show: true },
    { href: "/pings/", label: "Pings", icon: Inbox, show: true, count: inbox },
    { href: "/ask/", label: "Ask Alexandria", icon: Sparkles, show: has(me, "ask") },
    { href: "/documents/", label: "Knowledge base", icon: BookOpen, show: true },
    { href: "/review/", label: "Review queue", icon: ClipboardCheck, show: canReview, count: review },
    { href: "/audit/", label: "Audit ledger", icon: ScrollText, show: has(me, "view_audit") },
    { href: "/governance/", label: "Governance", icon: Users, show: has(me, "manage_access") },
    { href: "/security/", label: "Security", icon: KeyRound, show: true },
  ].filter((n) => n.show);

  return (
    <div className="flex h-screen flex-col md:flex-row">
      <aside className="flex shrink-0 flex-col border-b border-line bg-panel md:w-60 md:border-b-0 md:border-r">
        <div className="flex items-center gap-2 px-4 py-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand/20 text-brand"><ShieldCheck size={18} /></div>
          <span className="font-semibold">Alexandria</span>
          <span title={rt === "live" ? "Live updates connected" : "Reconnecting…"} className={`ml-auto h-2 w-2 rounded-full ${rt === "live" ? "bg-good" : "bg-warn"}`} />
          <span className="text-xs text-mute md:hidden">{me?.username} · L{me?.clearance}</span>
          <button className="btn !px-2 md:hidden" onClick={signOut} aria-label="Sign out"><LogOut size={14} /></button>
        </div>
        <nav aria-label="Main" className="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-1 md:flex-col md:overflow-visible">
          {nav.map(({ href, label, icon: Icon, count }) => {
            const active = path?.startsWith(href.slice(0, -1));
            return (
              <Link key={href} href={href} aria-current={active ? "page" : undefined}
                className={`flex shrink-0 items-center gap-2 rounded-lg px-3 py-2 text-sm ${active ? "bg-brand/15 text-ink" : "text-mute hover:bg-panel2 hover:text-ink"}`}>
                <Icon size={16} /> <span>{label}</span>
                {!!count && <span className="ml-auto rounded-full bg-brand px-1.5 text-xs text-white" aria-label={`${count} waiting`}>{count}</span>}
              </Link>
            );
          })}
        </nav>
        <div className="hidden border-t border-line p-3 md:block">
          <div className="text-sm font-medium">{me?.username}</div>
          <div className="mt-1 flex flex-wrap gap-1">
            <Badge tone="brand">{me?.label}</Badge>
            <Badge title="Your clearance level (0–100) decides which documents and messages you can read">Level {me?.clearance}</Badge>
            <Badge tone="good" title="Two-factor authentication is on">2FA ✓</Badge>
          </div>
          <div className="mt-1 text-xs text-mute">{me?.department}</div>
          <button className="btn mt-3 w-full" onClick={signOut}><LogOut size={14} /> Sign out</button>
        </div>
      </aside>
      <main className="min-h-0 flex-1 overflow-y-auto" id="main">{children}</main>
    </div>
  );
}
