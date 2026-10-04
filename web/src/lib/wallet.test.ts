import { describe, expect, it } from "vitest";
import bs58 from "bs58";
import nacl from "tweetnacl";
import { demoSigner } from "./wallet";

describe("demo wallet", () => {
  it("signs a message that verifies against its public key", async () => {
    const store: Record<string, string> = {};
    (globalThis as any).localStorage = { getItem: (k: string) => store[k] ?? null, setItem: (k: string, v: string) => { store[k] = v; } };
    const s = demoSigner(true)!;
    const msg = "Alexandria document approve\nstaged: 1";
    const sig = bs58.decode(await s.sign(msg));
    expect(nacl.sign.detached.verify(new TextEncoder().encode(msg), sig, bs58.decode(s.publicKey))).toBe(true);
    expect(nacl.sign.detached.verify(new TextEncoder().encode(msg + "x"), sig, bs58.decode(s.publicKey))).toBe(false);
    expect(demoSigner(false)!.publicKey).toBe(s.publicKey);          // persisted: same key next time
  });
});
