import type { Person } from "@/lib/api";

export function PersonAvailability({ person }: { person: Person }) {
  return <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-mute">
    {person.local_time && <span title={person.timezone ?? undefined}>{person.local_time} · {person.timezone?.split("/").pop()?.replace(/_/g, " ")}</span>}
    <span className={person.working ? "font-medium text-good" : ""}>{person.working === null ? "Hours not set" : person.working ? "Working now" : "Off hours"}</span>
    {person.online && <span className="font-medium text-good">Online</span>}
  </div>;
}
