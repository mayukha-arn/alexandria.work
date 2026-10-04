"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";

export default function Home() {
  const { status } = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (status === "authed") router.replace("/ask/");
    if (status === "anon") router.replace("/login/");
  }, [status, router]);
  return <p className="p-6 text-sm text-mute">Loading…</p>;
}
