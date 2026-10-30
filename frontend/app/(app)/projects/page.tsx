"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useApp } from "@/components/shell";
import { Card, Empty, ErrorNote, PageHeader, StatusBadge } from "@/components/ui";

export default function Projects() {
  const { canWrite } = useApp();
  const [list, setList] = useState<any[]>([]);
  const [sel, setSel] = useState<any>(null);
  const [f, setF] = useState({ name: "", aliases: "" });
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => api("/api/projects").then(setList), []);
  useEffect(() => { load(); }, [load]);
  return (
    <>
      <PageHeader title="Projects" sub="Sessions, tasks and decisions are linked to a project when its name or an alias is clearly the most-mentioned in a recording. Use aliases (e.g. codenames) to improve matching." />
      <ErrorNote error={err} />
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-4">
          {canWrite && (
            <form className="card space-y-3 p-4" onSubmit={async (e) => { e.preventDefault(); setErr(null); try { await api("/api/projects", { json: { name: f.name, aliases: f.aliases.split(",").map((x) => x.trim()).filter(Boolean) } }); setF({ name: "", aliases: "" }); load(); } catch (e: any) { setErr(e.message); } }}>
              <div><label className="label" htmlFor="pn">New project</label><input id="pn" className="input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
              <div><label className="label" htmlFor="pa">Aliases (comma-separated)</label><input id="pa" className="input" value={f.aliases} onChange={(e) => setF({ ...f, aliases: e.target.value })} /></div>
              <button className="btn btn-primary w-full">Create</button>
            </form>
          )}
          {!list.length ? <Empty title="No projects yet" /> : <div className="card divide-y divide-line">{list.map((p) => <button key={p.id} className={`block w-full px-4 py-3 text-left text-sm hover:bg-bg ${sel?.id === p.id ? "bg-bg" : ""}`} onClick={() => api(`/api/projects/${p.id}`).then(setSel)}>{p.name}{p.aliases.length ? <span className="text-xs text-mute"> · {p.aliases.join(", ")}</span> : null}</button>)}</div>}
        </div>
        <div className="space-y-6 lg:col-span-2">
          {!sel ? <Empty title="Select a project" hint="See its sessions, tasks and decisions." /> : (
            <>
              <Card title={`${sel.name} — recordings (${sel.sessions.length})`}>{!sel.sessions.length ? <p className="text-sm text-mute">No linked recordings.</p> : <ul className="space-y-2 text-sm">{sel.sessions.map((s: any) => <li key={s.id}><Link className="hover:text-accent" href={`/conversations/${s.id}`}>{s.title}</Link> <span className="text-xs text-mute">{new Date(s.occurred_at).toLocaleDateString()}</span> <StatusBadge status={s.status} /></li>)}</ul>}</Card>
              <Card title={`Decisions (${sel.decisions.length})`}>{!sel.decisions.length ? <p className="text-sm text-mute">None.</p> : <ul className="space-y-2 text-sm">{sel.decisions.map((d: any) => <li key={d.id}>{d.text}</li>)}</ul>}</Card>
              <Card title={`Tasks (${sel.tasks.length})`}>{!sel.tasks.length ? <p className="text-sm text-mute">None.</p> : <ul className="space-y-2 text-sm">{sel.tasks.map((t: any) => <li key={t.id}>{t.title} <span className="text-xs text-mute">· {t.assignee || "no owner"} · {t.status}</span></li>)}</ul>}</Card>
            </>
          )}
        </div>
      </div>
    </>
  );
}
