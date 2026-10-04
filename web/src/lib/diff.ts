// Turn a unified diff (as produced by Python's difflib) into side-by-side rows.
export type DiffRow = { left: string | null; right: string | null; kind: "same" | "changed" | "added" | "removed" };

export function parseDiff(diff: string): DiffRow[] {
  const rows: DiffRow[] = [];
  let removed: string[] = [];
  let added: string[] = [];
  const flush = () => {
    const n = Math.max(removed.length, added.length);
    for (let i = 0; i < n; i++) {
      const l = removed[i] ?? null;
      const r = added[i] ?? null;
      rows.push({ left: l, right: r, kind: l !== null && r !== null ? "changed" : l !== null ? "removed" : "added" });
    }
    removed = [];
    added = [];
  };
  for (const line of diff.split("\n")) {
    if (line.startsWith("---") || line.startsWith("+++") || line.startsWith("@@")) { flush(); continue; }
    if (line.startsWith("-")) removed.push(line.slice(1));
    else if (line.startsWith("+")) added.push(line.slice(1));
    else { flush(); if (line !== "") rows.push({ left: line.replace(/^ /, ""), right: line.replace(/^ /, ""), kind: "same" }); }
  }
  flush();
  return rows;
}

export const diffStats = (rows: DiffRow[]) => ({
  added: rows.filter((r) => r.right !== null && r.kind !== "same").length,
  removed: rows.filter((r) => r.left !== null && r.kind !== "same").length,
});
