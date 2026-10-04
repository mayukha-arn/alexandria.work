import { cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from "react";

export function Page({ title, subtitle, actions, children }: { title: string; subtitle?: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <div className="mx-auto flex h-full w-full max-w-6xl flex-col gap-4 p-4 md:p-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
          {subtitle && <p className="mt-0.5 text-sm text-mute">{subtitle}</p>}
        </div>
        {actions}
      </header>
      {children}
    </div>
  );
}

const TONES: Record<string, string> = {
  neutral: "border-transparent bg-panel2 text-mute", good: "border-transparent bg-good/10 text-good", warn: "border-transparent bg-warn/10 text-warn",
  bad: "border-transparent bg-bad/10 text-bad", brand: "border-transparent bg-brand/10 text-brand",
};
export function Badge({ children, tone = "neutral", title }: { children: ReactNode; tone?: keyof typeof TONES; title?: string }) {
  return <span title={title} className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${TONES[tone]}`}>{children}</span>;
}

export function ErrorBanner({ error }: { error: string | null }) {
  if (!error) return null;
  return <div role="alert" data-testid="error" className="rounded-lg border border-bad/50 bg-bad/10 px-3 py-2 text-sm text-bad">{error}</div>;
}

export function Notice({ tone = "warn", children }: { tone?: "warn" | "good" | "brand"; children: ReactNode }) {
  const c = { warn: "border-warn/40 bg-warn/10 text-warn", good: "border-good/40 bg-good/10 text-good", brand: "border-brand/40 bg-brand/10 text-ink" }[tone];
  return <div className={`rounded-lg border px-3 py-2 text-sm ${c}`}>{children}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-xl border border-dashed border-line px-4 py-10 text-center text-sm text-mute">{children}</div>;
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  // The label holds only its own text; the hint is linked with aria-describedby, so assistive
  // technology announces "Department" rather than "Department, only people at this level or above…".
  const uid = useId();
  const child = isValidElement(children) ? (children as ReactElement<{ id?: string }>) : null;
  const id = child?.props.id ?? uid;
  return (
    <div>
      <label htmlFor={id} className="label">{label}</label>
      {child ? cloneElement(child, { id, ...(hint ? { "aria-describedby": `${id}-hint` } : {}) } as object) : children}
      {hint && <span id={`${id}-hint`} className="mt-1 block text-xs text-mute">{hint}</span>}
    </div>
  );
}

export const Redacted = ({ text }: { text: string }) => (
  <span className="rounded border border-bad/40 bg-bad/10 px-1.5 py-0.5 font-mono text-xs text-bad">{text}</span>
);

export const Mono = ({ children }: { children: ReactNode }) => <span className="font-mono text-xs">{children}</span>;
