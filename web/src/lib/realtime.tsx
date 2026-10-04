"use client";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { apiBase, api, getToken, setToken } from "./api";

export type RtEvent = { type: string; [k: string]: any };
type Handler = (e: RtEvent) => void;
type Ctx = { status: "connecting" | "live" | "offline"; subscribe: (h: Handler) => () => void };

const RtCtx = createContext<Ctx>({ status: "offline", subscribe: () => () => {} });
export const useRealtime = () => useContext(RtCtx);

/** Subscribe to live events for as long as the component is mounted. */
export function useLive(handler: Handler) {
  const { subscribe } = useRealtime();
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => subscribe((e) => ref.current(e)), [subscribe]);
}

export function RealtimeProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Ctx["status"]>("connecting");
  const handlers = useRef(new Set<Handler>());

  const subscribe = useCallback((h: Handler) => {
    handlers.current.add(h);
    return () => { handlers.current.delete(h); };
  }, []);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let stopped = false;
    let retry = 1000;
    let timer: ReturnType<typeof setTimeout>;

    const connect = () => {
      if (stopped) return;
      setStatus("connecting");
      ws = new WebSocket(apiBase().replace(/^http/, "ws") + "/ws");
      ws.onopen = () => ws?.send(JSON.stringify({ type: "auth", token: getToken() }));
      ws.onmessage = (m) => {
        const ev = JSON.parse(m.data) as RtEvent;
        if (ev.type === "ready") { setStatus("live"); retry = 1000; return; }
        if (ev.type === "heartbeat") return;
        handlers.current.forEach((h) => h(ev));
      };
      ws.onclose = async (e) => {
        setStatus("offline");
        if (stopped) return;
        if (e.code === 4401) {
          // session ended or token expired: try to extend it, otherwise the next API call signs us out
          try {
            const r = await api<{ access_token: string }>("/auth/refresh", { method: "POST", quiet401: true });
            setToken(r.access_token);
          } catch { return; }
        }
        timer = setTimeout(connect, retry);
        retry = Math.min(retry * 2, 15000);
      };
    };
    connect();
    return () => { stopped = true; clearTimeout(timer); ws?.close(); };
  }, []);

  return <RtCtx.Provider value={{ status, subscribe }}>{children}</RtCtx.Provider>;
}
