// Display helpers for people: usernames like "maya.chen" become "Maya Chen", with a stable avatar colour.
const PALETTE = ["#6246EA", "#E11D74", "#0EA5E9", "#16A34A", "#F59E0B", "#7C3AED", "#DB2777", "#0891B2", "#EA580C", "#4F46E5"];

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
