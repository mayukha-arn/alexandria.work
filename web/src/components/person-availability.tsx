import type { Person } from "@/lib/api";

export function PersonAvailability({ person }: { person: Person }) {
  const available = person.working || person.online || person.on_call;
  return <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-mute">
    {person.local_time && <span title={person.timezone ?? undefined}>{person.local_time} · {person.timezone?.split("/").pop()?.replace(/_/g, " ")}</span>}
    <span className={available ? "font-medium text-good" : ""}>{person.working ? "Working now" : person.on_call ? "On call · Available now" : person.online ? "Online now" : person.working === null ? "Hours not set" : "Off hours"}</span>
  </div>;
}
