"use client";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { api, setToken, setUnauthorizedHandler, type Me, type Meta } from "./api";

type Status = "loading" | "anon" | "authed";
type Ctx = {
  status: Status; me: Me | null; meta: Meta | null;
  signIn: (token: string) => Promise<void>;
  signOut: () => Promise<void>;
  reloadMe: () => Promise<void>;
};

const AuthCtx = createContext<Ctx | null>(null);
export const useAuth = () => {
  const c = useContext(AuthCtx);
  if (!c) throw new Error("useAuth outside AuthProvider");
  return c;
};

const KEY = "alexandria.token";
const REFRESH_MS = 5 * 60 * 1000; // tokens last 15 min; extend the same session well before that

export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [status, setStatus] = useState<Status>("loading");
  const [me, setMe] = useState<Me | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const timer = useRef<ReturnType<typeof setInterval>>();

  const clear = useCallback(() => {
    setToken(null);
    try { sessionStorage.removeItem(KEY); } catch {}
    clearInterval(timer.current);
    setMe(null);
    setStatus("anon");
  }, []);

  const load = useCallback(async () => {
    const [m, mt] = await Promise.all([api<Me>("/auth/me"), api<Meta>("/meta")]);
    setMe(m);
    setMeta(mt);
    setStatus("authed");
    clearInterval(timer.current);
    timer.current = setInterval(async () => {
      try {
        const r = await api<{ access_token: string }>("/auth/refresh", { method: "POST" });
        setToken(r.access_token);
        try { sessionStorage.setItem(KEY, r.access_token); } catch {}
      } catch {
        /* a 401 here triggers the unauthorized handler below */
      }
    }, REFRESH_MS);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      clear();
      router.replace("/login/");
    });
    let saved: string | null = null;
    try { saved = sessionStorage.getItem(KEY); } catch {}
    if (!saved) { setStatus("anon"); return; }
    setToken(saved);
    load().catch(clear);
    return () => clearInterval(timer.current);
  }, [clear, load, router]);

  const signIn = useCallback(async (token: string) => {
    setToken(token);
    try { sessionStorage.setItem(KEY, token); } catch {}
    await load();
  }, [load]);

  const signOut = useCallback(async () => {
    try { await api("/auth/logout", { method: "POST", quiet401: true }); } catch {}
    clear();
    router.replace("/login/");
  }, [clear, router]);

  const reloadMe = useCallback(async () => { setMe(await api<Me>("/auth/me")); }, []);

  return <AuthCtx.Provider value={{ status, me, meta, signIn, signOut, reloadMe }}>{children}</AuthCtx.Provider>;
}
