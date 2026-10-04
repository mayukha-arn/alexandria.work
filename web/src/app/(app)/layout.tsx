"use client";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { BookOpen, ClipboardCheck, Hash, Inbox, KeyRound, LogOut, ScrollText, Search, ShieldCheck, Sparkles, UserCog, Users } from "lucide-react";
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
    <div className="flex h-screen items-center justify-center bg-white">
      <div className="ai-gradient flex h-12 w-12 animate-pulse items-center justify-center rounded-2xl text-white"><ShieldCheck /></div>
    </div>
  );
}

type NavItem = { href: string; label: string; short: string; icon: typeof Hash; show: boolean; count?: number; group: "work" | "govern" };

function Shell({ children }: { children: ReactNode }) {
  const { me } = useAuth();
  const { status: rt } = useRealtime();
  const path = usePathname();
  const router = useRouter();
  const [inbox, setInbox] = useState(0);
  const [review, setReview] = useState(0);
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

  // Keep the browser's time zone on file (once), so colleagues see your local time when routing questions.
  useEffect(() => {
    if (!me || me.timezone) return;
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (tz) api("/auth/me/timezone", { method: "PUT", body: { timezone: tz } }).catch(() => {});
  }, [me]);

  const nav: NavItem[] = useMemo(() => ([
    { href: "/ask/", label: "Ask Alexandria", short: "Ask", icon: Sparkles, show: true, group: "work" },
    { href: "/pings/", label: "Requests", short: "Requests", icon: Inbox, show: true, count: inbox, group: "work" },
    { href: "/documents/", label: "Knowledge base", short: "Knowledge", icon: BookOpen, show: true, group: "work" },
    { href: "/people/", label: "People", short: "People", icon: Users, show: true, group: "work" },
    { href: "/chat/", label: "Team spaces", short: "Spaces", icon: Hash, show: true, group: "work" },
    { href: "/review/", label: "Review queue", short: "Review", icon: ClipboardCheck, show: canReview, count: review, group: "govern" },
    { href: "/audit/", label: "Audit ledger", short: "Ledger", icon: ScrollText, show: has(me, "view_audit"), group: "govern" },
    { href: "/governance/", label: "Governance", short: "Access", icon: UserCog, show: has(me, "manage_access"), group: "govern" },
    { href: "/security/", label: "Security", short: "Security", icon: KeyRound, show: true, group: "govern" },
  ] as NavItem[]).filter((n) => n.show), [me, canReview, inbox, review]);

  const link = ({ href, label, short, icon: Icon, count }: NavItem) => {
    const active = path?.startsWith(href.slice(0, -1));
    return (
      <Link key={href} href={href} aria-label={label} aria-current={active ? "page" : undefined} title={label}
        className={`relative flex shrink-0 items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition ${active ? "bg-brand/10 text-brand" : "text-ink/75 hover:bg-side-hover hover:text-ink"}`}>
        <Icon size={17} strokeWidth={active ? 2.3 : 1.9} />
        <span className="md:hidden">{short}</span><span className="hidden md:inline">{label}</span>
        {!!count && <span className="ml-auto min-w-5 rounded-full bg-brand px-1.5 text-center text-[11px] font-bold leading-5 text-white" aria-label={`${count} waiting`}>{count}</span>}
      </Link>
    );
  };

  return (
    <div className="flex h-screen flex-col bg-white md:flex-row">
      <aside className="flex shrink-0 flex-col border-b border-line bg-side md:w-60 md:border-b-0 md:border-r">
        <div className="flex items-center gap-2.5 px-4 py-3 md:py-4">
          <Link href="/ask/" aria-label={`${ORG_NAME} home`} className="ai-gradient flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-white shadow-sm"><Sparkles size={18} /></Link>
          <div className="min-w-0 leading-tight"><div className="font-bold tracking-tight">Alexandria</div><div className="text-xs text-mute">{ORG_NAME}</div></div>
          <div className="relative ml-auto md:hidden"><AccountButton /></div>
        </div>
        <nav aria-label="Main" className="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-1 md:flex-col md:overflow-visible md:px-3">
          {nav.filter((n) => n.group === "work").map(link)}
          <div className="hidden px-3 pb-1 pt-4 text-[11px] font-semibold uppercase tracking-wider text-mute md:block">Trust &amp; governance</div>
          {nav.filter((n) => n.group === "govern").map(link)}
        </nav>
        <div className="relative hidden border-t border-line p-3 md:block"><AccountButton wide /></div>
      </aside>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-white px-4">
          <button onClick={() => setPalette(true)} aria-label="Search" aria-keyshortcuts="Meta+K" className="flex w-full max-w-xl items-center gap-2 rounded-lg border border-line bg-panel2 px-3 py-2 text-left text-sm text-mute transition hover:border-brand/40">
            <Search size={15} /> <span className="flex-1 truncate">Search knowledge, people, or ask a question…</span>
            <kbd className="hidden rounded border border-line bg-white px-1.5 text-[11px] font-medium sm:inline">⌘K</kbd>
          </button>
          <span className={`ml-auto hidden items-center gap-1.5 text-xs text-mute sm:flex`}><span className={`h-2 w-2 rounded-full ${rt === "live" ? "bg-good" : "bg-warn"}`} />{rt === "live" ? "Live" : "Reconnecting…"}</span>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto" id="main">{children}</main>
      </div>

      {palette && <Palette nav={nav} onClose={() => setPalette(false)} onGo={(href) => { setPalette(false); router.push(href); }} />}
    </div>
  );
}

function AccountButton({ wide = false }: { wide?: boolean }) {
  const { me, signOut } = useAuth();
  const { status: rt } = useRealtime();
  const [menu, setMenu] = useState(false);
  return (
    <>
      <button onClick={() => setMenu((m) => !m)} aria-label="Account" aria-expanded={menu}
        className={`flex items-center gap-2.5 rounded-lg text-left hover:bg-side-hover ${wide ? "w-full p-1.5" : "p-1"}`}>
        <Avatar name={me?.username} size={wide ? 34 : 30} presence={rt === "live" ? "online" : "away"} />
        {wide && <span className="min-w-0 leading-tight"><span className="block truncate text-sm font-semibold">{displayName(me?.username)}</span><span className="block truncate text-xs text-mute">{me?.label}</span></span>}
      </button>
      {menu && (
        <div role="menu" className="animate-in absolute right-0 top-full z-50 mt-1 w-72 rounded-xl border border-line bg-white p-4 text-ink shadow-2xl md:bottom-full md:left-0 md:right-auto md:top-auto md:mb-2 md:mt-0">
          <div className="flex items-center gap-3">
            <Avatar name={me?.username} size={44} />
            <div className="min-w-0">
              <div className="truncate font-semibold">{displayName(me?.username)}</div>
              <div className="text-xs capitalize text-mute">{me?.label} · {me?.department}</div>
            </div>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
            <div className="rounded-lg bg-panel2 px-2.5 py-2"><div className="text-mute">Clearance</div><div className="font-semibold">Level {me?.clearance}</div></div>
            <div className="rounded-lg bg-panel2 px-2.5 py-2"><div className="text-mute">Sign-in</div><div className="font-semibold text-good">2FA ✓</div></div>
          </div>
          {me?.timezone && <div className="mt-2 text-xs text-mute">Time zone: {me.timezone.replace(/_/g, " ")}</div>}
          <button role="menuitem" className="btn mt-3 w-full" onClick={signOut}><LogOut size={14} /> Sign out</button>
        </div>
      )}
    </>
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
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-[#06291f]/40 p-4 pt-[12vh] backdrop-blur-sm" onClick={onClose}>
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
