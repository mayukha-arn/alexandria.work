import { describe, expect, it } from "vitest";
import { parseSse } from "./sse";

describe("parseSse", () => {
  it("parses complete events and keeps the partial tail", () => {
    const text = 'event: meta\ndata: {"a":1}\n\nevent: token\ndata: {"text":"hi"}\n\nevent: tok';
    const { events, rest } = parseSse(text);
    expect(events).toEqual([{ event: "meta", data: { a: 1 } }, { event: "token", data: { text: "hi" } }]);
    expect(rest).toBe("event: tok");
  });
  it("reassembles an event split across chunks", () => {
    const first = parseSse('event: token\ndata: {"te');
    expect(first.events).toEqual([]);
    const second = parseSse(first.rest + 'xt":"ok"}\n\n');
    expect(second.events).toEqual([{ event: "token", data: { text: "ok" } }]);
  });
  it("ignores a malformed block without throwing", () => {
    expect(parseSse("event: x\ndata: {not json\n\n").events).toEqual([]);
  });
});
