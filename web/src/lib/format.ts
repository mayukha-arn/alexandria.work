export const fmtTime = (ts: number | null | undefined) =>
  ts ? new Date(ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";

export function timeAgo(ts: number | null | undefined): string {
  if (!ts) return "never";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export const initials = (name: string | null) => (name ?? "?").slice(0, 2).toUpperCase();

export const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, " ");

/** Classification labels the user may apply: those at or below their own clearance, lowest first. */
export function allowedLabels(classifications: Record<string, number>, clearance: number): string[] {
  return Object.entries(classifications).filter(([, v]) => v <= clearance).sort((a, b) => a[1] - b[1]).map(([k]) => k);
}

export const explorerUrl = (sig: string, apiUrl: string) => {
  const local = /127\.0\.0\.1|localhost/.test(apiUrl) || process.env.NEXT_PUBLIC_SOLANA_CLUSTER === "local";
  const cluster = process.env.NEXT_PUBLIC_SOLANA_CLUSTER ?? "devnet";
  return local
    ? `https://explorer.solana.com/tx/${sig}?cluster=custom&customUrl=${encodeURIComponent("http://127.0.0.1:8899")}`
    : `https://explorer.solana.com/tx/${sig}?cluster=${cluster}`;
};

/** A document's display title from its source label: "file:Payroll Calendar.pdf" -> "Payroll Calendar". */
export const docTitle = (source: string) =>
  source.replace(/^thread:\d+ /, "").replace(/^file:/, "").replace(/\.pdf$/i, "").trim() || "document";
