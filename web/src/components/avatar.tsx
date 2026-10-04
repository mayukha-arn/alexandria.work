import { Sparkles } from "lucide-react";
import { colorOf, displayName, initialsOf } from "@/lib/people";

export function Avatar({ name, size = 36, presence }: { name: string | null | undefined; size?: number; presence?: "online" | "away" | null }) {
  return (
    <span className="relative inline-flex shrink-0" style={{ width: size, height: size }} title={displayName(name)}>
      <span className="flex h-full w-full items-center justify-center rounded-lg font-semibold text-white"
        style={{ background: colorOf(name), fontSize: Math.round(size * 0.38) }}>{initialsOf(name)}</span>
      {presence && <span className={`absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-white ${presence === "online" ? "bg-good" : "bg-warn"}`} />}
    </span>
  );
}

export function AiAvatar({ size = 36 }: { size?: number }) {
  return (
    <span className="ai-gradient inline-flex shrink-0 items-center justify-center rounded-lg text-white shadow-sm" style={{ width: size, height: size }} title="Alexandria">
      <Sparkles size={Math.round(size * 0.5)} />
    </span>
  );
}
