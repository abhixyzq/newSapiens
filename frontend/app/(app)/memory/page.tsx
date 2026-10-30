"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useApp } from "@/components/shell";
import { Badge, Card, Empty, ErrorNote, PageHeader } from "@/components/ui";

export default function Memory() {
  const { canWrite } = useApp();
  const [ents, setEnts] = useState<any[]>([]);
  const [sel, setSel] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => api("/api/memory/entities").then(setEnts), []);
  useEffect(() => { load(); }, [load]);
  const byId = Object.fromEntries(ents.map((e) => [e.id, e]));
  return (
    <>
      <PageHeader title="Knowledge Memory" sub="People and projects Sapiens has seen. Entities are matched by exact name only; similar names are kept separate and flagged for you to merge or confirm." />
      <ErrorNote error={err} />
      {!ents.length ? <Empty title="Memory is empty" hint="People and projects appear here after your first processed conversation." /> : (
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="card divide-y divide-line lg:col-span-1">
            {ents.map((e) => (
              <button key={e.id} className={`block w-full px-4 py-3 text-left text-sm hover:bg-bg ${sel?.id === e.id ? "bg-bg" : ""}`}
                onClick={async () => setSel({ ...e, mem: e.type === "person" ? await api(`/api/memory/people/${encodeURIComponent(e.name)}`) : null })}>
                {e.name} <span className="text-xs text-mute">· {e.type}</span> {e.needs_review && <Badge tone="warn">review</Badge>}
              </button>
            ))}
          </div>
          <div className="space-y-6 lg:col-span-2">
            {!sel ? <Empty title="Select an entity" /> : (
              <>
                <Card title={sel.name} right={sel.needs_review && canWrite && <button className="btn" onClick={() => api(`/api/memory/entities/${sel.id}`, { method: "PATCH", json: { needs_review: false } }).then(load).then(() => setSel({ ...sel, needs_review: false })).catch((e) => setErr(e.message))}>Mark as distinct person</button>}>
                  {sel.aliases?.length > 0 && <p className="text-sm text-mute">Also known as: {sel.aliases.join(", ")}</p>}
                  {sel.similar_to?.length > 0 && (
                    <div className="mt-2 rounded-lg border border-warn/30 bg-warn/10 p-3 text-sm">Possibly the same as: {sel.similar_to.map((id: string) => byId[id]).filter(Boolean).map((o: any) => (
                      <span key={o.id} className="mr-2">{o.name} {canWrite && <button className="text-accent" onClick={async () => { if (confirm(`Merge “${sel.name}” into “${o.name}”?`)) { try { await api(`/api/memory/entities/${sel.id}/merge`, { json: { into_id: o.id } }); setSel(null); load(); } catch (e: any) { setErr(e.message); } } }}>(merge into this)</button>}</span>))}</div>)}
                </Card>
                {sel.mem && (<>
                  <Card title={`Tasks (${sel.mem.tasks.length})`}>{!sel.mem.tasks.length ? <p className="text-sm text-mute">None.</p> : <ul className="space-y-2 text-sm">{sel.mem.tasks.map((t: any) => <li key={t.id}>{t.title} <span className="text-xs text-mute">· {t.status}</span> {t.session_id && <Link className="text-xs text-accent" href={`/conversations/${t.session_id}`}>source</Link>}</li>)}</ul>}</Card>
                  <Card title={`Commitments (${sel.mem.commitments.length})`}>{!sel.mem.commitments.length ? <p className="text-sm text-mute">None.</p> : <ul className="space-y-2 text-sm">{sel.mem.commitments.map((c: any) => <li key={c.id}>{c.text} <span className="text-xs text-mute">· {c.status}</span> <Link className="text-xs text-accent" href={`/conversations/${c.session_id}`}>source</Link></li>)}</ul>}</Card>
                </>)}
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
