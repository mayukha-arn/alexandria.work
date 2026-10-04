// Display helpers for people: usernames like "maya.chen" become "Maya Chen", with a stable avatar colour.
const PALETTE = ["#059669", "#0D9488", "#0284C7", "#16A34A", "#CA8A04", "#EA580C", "#DC2626", "#0891B2", "#65A30D", "#475569"];

export function displayName(username: string | null | undefined): string {
  if (!username) return "Someone";
  return username.split(/[._-]+/).filter(Boolean).map((p) => p[0].toUpperCase() + p.slice(1)).join(" ");
}

export function initialsOf(username: string | null | undefined): string {
  const parts = displayName(username).split(" ");
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}

export function colorOf(key: string | null | undefined): string {
  let h = 0;
  for (const ch of key ?? "") h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

export const ORG_NAME = process.env.NEXT_PUBLIC_ORG_NAME ?? "Meridian";
