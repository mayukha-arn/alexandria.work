// A small, safe Markdown renderer for AI answers: headings, bullet/numbered lists, **bold**, `code`, code
// blocks and [n] citation chips. It builds React elements only - never raw HTML - so nothing in a model's
// output can inject markup or script.
import { Fragment, type ReactNode } from "react";

function inline(text: string, onCite?: (n: number) => void): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\[\d{1,2}\])/g;
  let last = 0, m: RegExpExecArray | null, k = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    if (t.startsWith("**")) out.push(<strong key={k++} className="font-semibold">{t.slice(2, -2)}</strong>);
    else if (t.startsWith("`")) out.push(<code key={k++} className="rounded bg-panel2 px-1 py-0.5 font-mono text-[0.85em]">{t.slice(1, -1)}</code>);
    else {
      const n = Number(t.slice(1, -1));
      out.push(<button key={k++} type="button" onClick={() => onCite?.(n)} className="mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded bg-brand/10 px-1 align-text-top text-[10px] font-semibold text-brand hover:bg-brand/20">{n}</button>);
    }
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ text, onCite }: { text: string; onCite?: (n: number) => void }) {
  const lines = text.replace(/\r/g, "").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0, k = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) code.push(lines[i++]);
      i++;
      blocks.push(<pre key={k++} className="my-2 overflow-x-auto rounded-lg bg-[#16122e] p-3 font-mono text-xs leading-relaxed text-[#e9e6ff]">{code.join("\n")}</pre>);
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*]|\d+\.)\s+/, ""));
      const L = ordered ? "ol" : "ul";
      blocks.push(<L key={k++} className={`my-1.5 space-y-1 pl-5 ${ordered ? "list-decimal" : "list-disc"} marker:text-mute`}>{items.map((it, j) => <li key={j}>{inline(it, onCite)}</li>)}</L>);
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) { blocks.push(<p key={k++} className="mb-1 mt-2 font-semibold">{inline(h[2], onCite)}</p>); i++; continue; }
    if (!line.trim()) { i++; continue; }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(```|#{1,4}\s|\s*([-*]|\d+\.)\s+)/.test(lines[i])) para.push(lines[i++]);
    blocks.push(<p key={k++} className="my-1.5">{para.map((p, j) => <Fragment key={j}>{j > 0 && <br />}{inline(p, onCite)}</Fragment>)}</p>);
  }
  return <div className="text-[14px] leading-relaxed">{blocks}</div>;
}
