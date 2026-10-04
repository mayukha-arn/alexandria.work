// Solana wallets. Phantom (or any wallet exposing window.solana) is the real thing. The "demo wallet"
// is an ed25519 key kept in this browser so the app can be tried without installing anything: it
// signs exactly the same messages, but it is not a secure place to keep a key.
import bs58 from "bs58";
import nacl from "tweetnacl";

export type Signer = { kind: "phantom" | "demo"; publicKey: string; sign: (message: string) => Promise<string> };

const DEMO_KEY = "alexandria.demo-wallet";

type Provider = {
  isPhantom?: boolean;
  publicKey?: { toString(): string };
  connect: () => Promise<{ publicKey: { toString(): string } }>;
  signMessage: (m: Uint8Array, enc?: string) => Promise<{ signature: Uint8Array } | Uint8Array>;
};

function provider(): Provider | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { phantom?: { solana?: Provider }; solana?: Provider };
  return w.phantom?.solana ?? w.solana ?? null;
}

export const phantomAvailable = () => provider() !== null;

export async function connectPhantom(): Promise<Signer> {
  const p = provider();
  if (!p) throw new Error("No Solana wallet found. Install Phantom, or use the demo wallet.");
  const { publicKey } = await p.connect();
  return {
    kind: "phantom",
    publicKey: publicKey.toString(),
    sign: async (message) => {
      const out = await p.signMessage(new TextEncoder().encode(message), "utf8");
      const sig = out instanceof Uint8Array ? out : out.signature;
      return bs58.encode(sig);
    },
  };
}

function loadDemoSecret(): Uint8Array | null {
  try {
    const raw = localStorage.getItem(DEMO_KEY);
    return raw ? bs58.decode(raw) : null;
  } catch {
    return null;
  }
}

export const hasDemoWallet = () => loadDemoSecret() !== null;

export function demoSigner(create = false): Signer | null {
  let secret = loadDemoSecret();
  if (!secret) {
    if (!create) return null;
    secret = nacl.sign.keyPair().secretKey;
    localStorage.setItem(DEMO_KEY, bs58.encode(secret));
  }
  const kp = nacl.sign.keyPair.fromSecretKey(secret);
  return {
    kind: "demo",
    publicKey: bs58.encode(kp.publicKey),
    sign: async (message) => bs58.encode(nacl.sign.detached(new TextEncoder().encode(message), kp.secretKey)),
  };
}

/** The signer matching the wallet linked to the account, or an explanation of what is missing. */
export async function signerFor(linkedPubkey: string | null): Promise<Signer> {
  if (!linkedPubkey) throw new Error("Link a Solana wallet first (Security page).");
  const demo = demoSigner(false);
  if (demo && demo.publicKey === linkedPubkey) return demo;
  if (phantomAvailable()) {
    const p = await connectPhantom();
    if (p.publicKey === linkedPubkey) return p;
    throw new Error("The connected wallet is not the one linked to your account.");
  }
  throw new Error("The wallet linked to your account is not available in this browser.");
}

export const shortKey = (k: string | null) => (k ? `${k.slice(0, 4)}…${k.slice(-4)}` : "—");
