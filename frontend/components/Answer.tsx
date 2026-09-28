// Safe, minimal formatting for model output: paragraphs, "- " / "1. " lists, **bold**, [n] citations.
import type { ReactNode } from "react";

/** Cited passage numbers in order of first mention; shown to the reader as 1, 2, 3... */
export function citationOrder(text: string, numSources: number): number[] {
  const order: number[] = [];
  for (const m of text.matchAll(/\[(\d+)\]/g)) {
    const n = Number(m[1]);
    if (n >= 1 && n <= numSources && !order.includes(n)) order.push(n);
  }
  return order;
}

function inline(text: string, order: number[], onCite: (n: number) => void, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(/(\*\*[^*]+\*\*|\[\d+\])/g)) {
    const i = m.index ?? 0;
    if (i > last) out.push(text.slice(last, i));
    const token = m[0];
    if (token.startsWith("**")) out.push(<strong key={`${keyPrefix}-${i}`}>{token.slice(2, -2)}</strong>);
    else {
      const n = Number(token.slice(1, -1));
      const shown = order.indexOf(n) + 1;
      if (shown) out.push(
        <button key={`${keyPrefix}-${i}`} type="button" className="cite" title={`Source ${shown}`} onClick={() => onCite(n)}>{shown}</button>,
      );
    }
    last = i + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export default function Answer({ text, numSources, streaming, onCite }: { text: string; numSources: number; streaming: boolean; onCite: (n: number) => void }) {
  const order = citationOrder(text, numSources);
  const blocks = text.trim().split(/\n\s*\n/).filter(Boolean);
  const caret = streaming ? <span className="caret" /> : null;
  return (
    <div className="content">
      {blocks.map((block, b) => {
        const lines = block.split("\n").filter((l) => l.trim());
        const isList = lines.length > 0 && lines.every((l) => /^\s*([-*•]|\d+[.)])\s+/.test(l));
        const last = b === blocks.length - 1;
        if (isList) {
          const Tag = /^\s*\d/.test(lines[0]) ? "ol" : "ul";
          return (
            <Tag key={b}>
              {lines.map((l, i) => (
                <li key={i}>{inline(l.replace(/^\s*([-*•]|\d+[.)])\s+/, ""), order, onCite, `${b}.${i}`)}{last && i === lines.length - 1 && caret}</li>
              ))}
            </Tag>
          );
        }
        return <p key={b}>{inline(lines.join("\n"), order, onCite, `${b}`)}{last && caret}</p>;
      })}
      {!blocks.length && caret}
    </div>
  );
}
