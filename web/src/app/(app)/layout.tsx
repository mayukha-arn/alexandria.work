"use client";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { BookOpen, ClipboardCheck, Hash, Inbox, KeyRound, LogOut, ScrollText, Search, ShieldCheck, Sparkles, Users } from "lucide-react";
import { api, has, type Pending, type Ping } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { RealtimeProvider, useLive, useRealtime } from "@/lib/realtime";
import { displayName, ORG_NAME } from "@/lib/people";
import { Avatar } from "@/components/avatar";

export default function AppLayout({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();
  useEffect(() => { if (status === "anon") router.replace("/login/"); }, [status, router]);
  if (status !== "authed") return <Splash />;
  return <RealtimeProvider><Shell>{children}</Shell></RealtimeProvider>;
}

function Splash() {
  return (
    <div className="flex h-screen items-center justify-center bg-rail">
      <div className="ai-gradient flex h-12 w-12 animate-pulse items-center justify-center rounded-2xl text-white"><ShieldCheck /></div>
    </div>
  );
}

type NavItem = { href: string; label: string; short: string; icon: typeof Hash; show: boolean; count?: number };

function Shell({ children }: { children: ReactNode }) {
  const { me, signOut } = useAuth();
  const { status: rt } = useRealtime();
  const path = usePathname();
  const router = useRouter();
  const [inbox, setInbox] = useState(0);
  const [review, setReview] = useState(0);
  const [menu, setMenu] = useState(false);
  const [palette, setPalette] = useState(false);
  const canReview = has(me, "approve_doc");

  const load = useCallback(() => {
    api<Ping[]>("/pings?box=inbox").then((p) => setInbox(p.filter((x) => x.status === "open").length)).catch(() => {});
    if (canReview) api<Pending[]>("/documents/pending").then((p) => setReview(p.length)).catch(() => {});
  }, [canReview]);
  useEffect(load, [load]);
  useLive((e) => { if (e.type.startsWith("ping.")) load(); });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette((v) => !v); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const nav: NavItem[] = useMemo(() => [
    { href: "/chat/", label: "Chat", short: "Chat", icon: Hash, show: true },
    { href: "/pings/", label: "Pings", short: "Pings", icon: Inbox, show: true, count: inbox },
    { href: "/ask/", label: "Ask Alexandria", short: "Assistant", icon: Sparkles, show: has(me, "ask") },
    { href: "/documents/", label: "Knowledge base", short: "Files", icon: BookOpen, show: true },
    { href: "/review/", label: "Review queue", short: "Review", icon: ClipboardCheck, show: canReview, count: review },
    { href: "/audit/", label: "Audit ledger", short: "Ledger", icon: ScrollText, show: has(me, "view_audit") },
    { href: "/governance/", label: "Governance", short: "Admin", icon: Users, show: has(me, "manage_access") },
    { href: "/security/", label: "Security", short: "Security", icon: KeyRound, show: true },
  ].filter((n) => n.show), [me, canReview, inbox, review]);

  return (
    <div className="flex h-screen flex-col bg-white md:flex-row">
      {/* app rail */}
      <aside className="flex shrink-0 items-center gap-1 bg-rail px-2 py-2 md:w-[72px] md:flex-col md:px-0 md:py-3">
        <Link href="/chat/" aria-label={`${ORG_NAME} workspace`} className="ai-gradient mb-0 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl font-bold text-white shadow-lg md:mb-3">
          {ORG_NAME[0]}
        </Link>
        <nav aria-label="Main" className="flex flex-1 gap-1 overflow-x-auto md:flex-col md:items-center md:overflow-visible">
          {nav.map(({ href, label, short, icon: Icon, count }) => {
            const active = path?.startsWith(href.slice(0, -1));
            return (
              <Link key={href} href={href} aria-label={label} aria-current={active ? "page" : undefined} title={label}
                className={`group relative flex w-14 shrink-0 flex-col items-center gap-0.5 rounded-xl py-1.5 text-[10px] font-medium transition ${active ? "bg-side-active text-white" : "text-side-mute hover:bg-side-hover hover:text-side-ink"}`}>
                <Icon size={19} strokeWidth={active ? 2.3 : 1.9} />
                <span>{short}</span>
                {!!count && <span className="absolute right-1.5 top-0.5 min-w-4 rounded-full bg-brand2 px-1 text-center text-[10px] font-bold leading-4 text-white" aria-label={`${count} waiting`}>{count}</span>}
              </Link>
            );
          })}
        </nav>
        <div className="relative md:mt-2">
          <button onClick={() => setMenu((m) => !m)} aria-label="Account" aria-expanded={menu} className="rounded-xl p-1 hover:bg-side-hover">
            <Avatar name={me?.username} size={36} presence={rt === "live" ? "online" : "away"} />
          </button>
          {menu && (
            <div role="menu" className="animate-in absolute bottom-0 right-0 z-50 w-72 rounded-xl border border-line bg-white p-4 text-ink shadow-2xl md:bottom-0 md:left-14 md:right-auto">
              <div className="flex items-center gap-3">
                <Avatar name={me?.username} size={44} />
                <div className="min-w-0">
                  <div className="truncate font-semibold">{displayName(me?.username)}</div>
                  <div className="text-xs text-mute">{me?.label} · {me?.department}</div>
                </div>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                <div className="rounded-lg bg-panel2 px-2.5 py-2"><div className="text-mute">Clearance</div><div className="font-semibold">Level {me?.clearance}</div></div>
                <div className="rounded-lg bg-panel2 px-2.5 py-2"><div className="text-mute">Sign-in</div><div className="font-semibold text-good">2FA ✓</div></div>
              </div>
              <div className="mt-2 flex items-center gap-2 text-xs text-mute"><span className={`h-2 w-2 rounded-full ${rt === "live" ? "bg-good" : "bg-warn"}`} />{rt === "live" ? "Connected" : "Reconnecting…"}</div>
              <button role="menuitem" className="btn mt-3 w-full" onClick={signOut}><LogOut size={14} /> Sign out</button>
            </div>
          )}
        </div>
      </aside>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {/* top bar */}
        <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line bg-white px-3">
          <button onClick={() => setPalette(true)} aria-label="Search" aria-keyshortcuts="Meta+K" className="mx-auto flex w-full max-w-xl items-center gap-2 rounded-lg border border-line bg-panel2 px-3 py-1.5 text-left text-sm text-mute transition hover:border-brand/40">
            <Search size={15} /> <span className="flex-1 truncate">Search or ask Alexandria…</span>
            <kbd className="hidden rounded border border-line bg-white px-1.5 text-[11px] font-medium sm:inline">⌘K</kbd>
          </button>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto" id="main">{children}</main>
      </div>

      {palette && <Palette nav={nav} onClose={() => setPalette(false)} onGo={(href) => { setPalette(false); router.push(href); }} />}
      {menu && <div className="fixed inset-0 z-40" onClick={() => setMenu(false)} aria-hidden />}
    </div>
  );
}

function Palette({ nav, onClose, onGo }: { nav: NavItem[]; onClose: () => void; onGo: (href: string) => void }) {
  const { me } = useAuth();
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);
  const items = [
    ...(q.trim() && has(me, "ask") ? [{ key: "ask", label: `Ask Alexandria: “${q.trim()}”`, icon: Sparkles, href: `/ask/?q=${encodeURIComponent(q.trim())}` }] : []),
    ...nav.filter((n) => n.label.toLowerCase().includes(q.toLowerCase())).map((n) => ({ key: n.href, label: `Go to ${n.label}`, icon: n.icon, href: n.href })),
  ];
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-[#100c26]/40 p-4 pt-[12vh] backdrop-blur-sm" onClick={onClose}>
      <div role="dialog" aria-label="Command palette" className="animate-in w-full max-w-xl overflow-hidden rounded-2xl border border-line bg-white shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 border-b border-line px-4">
          <Search size={16} className="text-mute" />
          <input ref={input} value={q} onChange={(e) => { setQ(e.target.value); setSel(0); }} placeholder="Ask a question or jump to…" aria-label="Command"
            className="h-12 flex-1 bg-transparent text-[15px] outline-none"
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, items.length - 1)); }
              if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
              if (e.key === "Enter" && items[sel]) onGo(items[sel].href);
            }} />
        </div>
        <ul className="max-h-80 overflow-y-auto p-2">
          {items.map((it, i) => (
            <li key={it.key}>
              <button onMouseEnter={() => setSel(i)} onClick={() => onGo(it.href)} className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm ${i === sel ? "bg-brand/10 text-ink" : "text-ink/80"}`}>
                <it.icon size={16} className={it.key === "ask" ? "text-brand" : "text-mute"} /> {it.label}
              </button>
            </li>
          ))}
          {!items.length && <li className="px-3 py-6 text-center text-sm text-mute">No matches</li>}
        </ul>
      </div>
    </div>
  );
}
