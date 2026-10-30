"use client";
import Link from "next/link";
import { Fragment, ReactNode } from "react";
import { fmtTs } from "@/lib/format";
import { parseBlocks } from "@/lib/md";

export type Citation = { ref: string; type: string; label: string; snippet: string; start_ms: number | null; session_id: string | null; audio_url: string | null; audio_start_s?: number; available: boolean };

function inline(text: string, cites: Citation[], onCite: (c: Citation) => void): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*|_[^_]+_|\[S\d+\])/g);
  return parts.map((p, i) => {
    if (/^\*\*.+\*\*$/.test(p)) return <strong key={i}>{p.slice(2, -2)}</strong>;
    if (/^_.+_$/.test(p)) return <em key={i} className="text-mute">{p.slice(1, -1)}</em>;
    const m = p.match(/^\[(S\d+)\]$/);
    if (m) {
      const c = cites.find((x) => x.ref === m[1]);
      return (
        <button key={i} onClick={() => c && onCite(c)} disabled={!c}
          className="mx-0.5 rounded bg-accent/10 px-1 align-baseline text-[11px] font-semibold text-accent hover:bg-accent/20" title={c?.label}>{m[1]}</button>
      );
    }
    return <Fragment key={i}>{p}</Fragment>;
  });
}

export function Answer({ content, citations, onCite }: { content: string; citations: Citation[]; onCite: (c: Citation) => void }) {
  return (
    <div className="space-y-3 text-sm leading-relaxed">
      {parseBlocks(content).map((b, i) =>
        b.type === "p" ? <p key={i} className="whitespace-pre-line">{inline(b.text, citations, onCite)}</p>
        : b.type === "ul" ? <ul key={i} className="list-disc space-y-1.5 pl-5">{b.items.map((t, j) => <li key={j}>{inline(t, citations, onCite)}</li>)}</ul>
        : (
          <div key={i} className="overflow-x-auto"><table className="w-full text-left text-xs">
            <thead><tr>{b.head.map((h, j) => <th key={j} className="border-b border-line px-2 py-1.5 font-semibold">{h}</th>)}</tr></thead>
            <tbody>{b.rows.map((r, j) => <tr key={j}>{r.map((c, k) => <td key={k} className="border-b border-line/60 px-2 py-1.5">{c}</td>)}</tr>)}</tbody>
          </table></div>
        ))}
    </div>
  );
}

export function SourceList({ citations, onCite }: { citations: Citation[]; onCite: (c: Citation) => void }) {
  if (!citations.length) return null;
  return (
    <div className="mt-4 border-t border-line pt-3">
      <div className="h-section mb-2">Sources</div>
      <ul className="space-y-1.5">
        {citations.map((c) => (
          <li key={c.ref} className="text-xs">
            <button onClick={() => onCite(c)} className="text-left hover:text-accent">
              <span className="mr-2 rounded bg-accent/10 px-1 font-semibold text-accent">{c.ref}</span>
              <span className="text-mute">{c.type}</span> · {c.label}{c.start_ms != null && c.available ? ` · @${fmtTs(c.start_ms)}` : ""}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function SourcePanel({ c, onClose }: { c: Citation; onClose: () => void }) {
  return (
    <aside className="card fixed bottom-4 right-4 z-40 w-[min(26rem,calc(100vw-2rem))] p-4 shadow-2xl" role="dialog" aria-label="Source">
      <div className="flex items-start justify-between gap-3">
        <div><span className="mr-2 rounded bg-accent/10 px-1 text-xs font-semibold text-accent">{c.ref}</span><span className="text-xs text-mute">{c.type}</span>
          <div className="mt-1 text-sm font-medium">{c.label}</div></div>
        <button className="btn px-2 py-0.5" onClick={onClose} aria-label="Close">✕</button>
      </div>
      {c.available ? (
        <>
          {c.start_ms != null && <div className="mt-2 text-xs text-mute">Timestamp {fmtTs(c.start_ms)}</div>}
          <blockquote className="mt-2 max-h-48 overflow-y-auto whitespace-pre-line border-l-2 border-accent pl-3 text-sm">{c.snippet}</blockquote>
          {c.audio_url && <audio className="mt-3 w-full" controls preload="none" src={`${c.audio_url}#t=${c.audio_start_s ?? 0}`} />}
          {c.session_id && <Link className="mt-3 inline-block text-sm text-accent" href={`/conversations/${c.session_id}${c.start_ms != null ? `?t=${c.start_ms}` : ""}`}>Open full transcript →</Link>}
        </>
      ) : <p className="mt-2 text-sm text-mute">This source has been deleted or is no longer shared with you.</p>}
    </aside>
  );
}
