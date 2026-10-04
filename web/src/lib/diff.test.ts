import { describe, expect, it } from "vitest";
import { diffStats, parseDiff } from "./diff";

const DIFF = ["--- aaa", "+++ bbb", "@@ -1,3 +1,3 @@", " keep one", "-old line", "+new line", " keep two", "-gone", "+x", "+extra"].join("\n");

describe("parseDiff", () => {
  it("pairs removed and added lines side by side", () => {
    const rows = parseDiff(DIFF);
    expect(rows).toEqual([
      { left: "keep one", right: "keep one", kind: "same" },
      { left: "old line", right: "new line", kind: "changed" },
      { left: "keep two", right: "keep two", kind: "same" },
      { left: "gone", right: "x", kind: "changed" },
      { left: null, right: "extra", kind: "added" },
    ]);
  });
  it("shows a brand-new document as all additions", () => {
    const rows = parseDiff("--- (new document)\n+++ abc\n+line 1\n+line 2");
    expect(rows.every((r) => r.kind === "added" && r.left === null)).toBe(true);
    expect(rows).toHaveLength(2);
  });
  it("handles removals with nothing added", () => {
    expect(parseDiff("-a\n-b")).toEqual([{ left: "a", right: null, kind: "removed" }, { left: "b", right: null, kind: "removed" }]);
  });
  it("is empty for an empty diff", () => {
    expect(parseDiff("")).toEqual([]);
  });
  it("counts changes", () => {
    expect(diffStats(parseDiff(DIFF))).toEqual({ added: 3, removed: 2 });
  });
});
