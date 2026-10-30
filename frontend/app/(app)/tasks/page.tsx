"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useApp } from "@/components/shell";
import { Badge, Card, Empty, ErrorNote, PageHeader, Spinner, StatusBadge } from "@/components/ui";

const STATUSES = ["detected", "needs_confirmation", "pending", "in_progress", "completed", "cancelled"];

export default function Tasks() {
  const { canWrite } = useApp();
  const [tab, setTab] = useState<"open" | "review" | "done">("open");
  const [data, setData] = useState<any>(null);
  const [commit, setCommit] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [n, setN] = useState({ title: "", assignee: "", due_date: "" });
  const [edit, setEdit] = useState<string | null>(null);
  const [ed, setEd] = useState({ assignee: "", due_date: "" });

  const load = useCallback(async () => {
    const st = tab === "open" ? "pending,in_progress" : tab === "review" ? "detected,needs_confirmation" : "completed,cancelled";
    setData(await api(`/api/tasks?status=${st}&limit=100`));
    setCommit(await api("/api/intelligence/commitments?status=open"));
  }, [tab]);
  useEffect(() => { load().catch((e) => setErr(e.message)); }, [load]);
  const act = async (fn: () => Promise<any>) => { setErr(null); try { await fn(); await load(); } catch (e: any) { setErr(e.message); } };
  const patch = (id: string, json: any) => act(() => api(`/api/tasks/${id}`, { method: "PATCH", json }));

  return (
    <>
      <PageHeader title="Tasks & Commitments" sub="AI-detected items stay in “Needs review” until you confirm them. Nothing is assigned or sent to anyone automatically." />
      <ErrorNote error={err} />
      {canWrite && (
        <form className="card mb-6 flex flex-wrap items-end gap-3 p-4" onSubmit={(e) => { e.preventDefault(); if (!n.title.trim()) return; act(async () => { await api("/api/tasks", { json: { title: n.title, assignee: n.assignee || undefined, due_date: n.due_date || undefined } }); setN({ title: "", assignee: "", due_date: "" }); }); }}>
          <div className="min-w-[14rem] flex-1"><label className="label" htmlFor="tt">New task</label><input id="tt" className="input" value={n.title} onChange={(e) => setN({ ...n, title: e.target.value })} /></div>
          <div><label className="label" htmlFor="ta">Owner</label><input id="ta" className="input" value={n.assignee} onChange={(e) => setN({ ...n, assignee: e.target.value })} /></div>
          <div><label className="label" htmlFor="td">Due</label><input id="td" type="date" className="input" value={n.due_date} onChange={(e) => setN({ ...n, due_date: e.target.value })} /></div>
          <button className="btn btn-primary">Add</button>
        </form>
      )}
      <div role="tablist" className="mb-4 flex gap-1 text-sm">
        {([["open", "Open"], ["review", "Needs review"], ["done", "Done"]] as const).map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)} className={`rounded-lg px-3 py-1.5 ${tab === k ? "bg-navy text-white dark:bg-accent dark:text-[#04121c]" : "text-mute hover:bg-panel"}`}>{l}</button>)}
      </div>
      {!data ? <Spinner /> : !data.items.length ? <Empty title="No tasks here" hint={tab === "review" ? "Tasks detected from conversations will wait here for you to confirm owner and deadline." : undefined} /> : (
        <div className="card divide-y divide-line">
          {data.items.map((t: any) => (
            <div key={t.id} className="px-4 py-3">
              <div className="flex flex-wrap items-start gap-3">
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium">{t.title}</div>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-mute">
                    <span>{t.assignee ? `Owner: ${t.assignee}` : "No owner identified"}{t.assignee_evidence === "first_person" ? " (speaker, inferred)" : ""}</span>
                    {(t.due_date || t.due_text) && <span>· due {t.due_date || `“${t.due_text}” (no fixed date)`}</span>}
                    <Badge tone={t.priority === "high" ? "bad" : "mute"}>{t.priority}</Badge><StatusBadge status={t.status} />
                    {t.source === "ai" && <Badge tone="info">AI · {Math.round(t.confidence * 100)}%</Badge>}
                    {t.session_id && <Link className="text-accent" href={`/conversations/${t.session_id}?t=${t.timestamp_ms ?? 0}`}>source</Link>}
                  </div>
                </div>
                {canWrite && (
                  <div className="flex flex-wrap items-center gap-2">
                    {["detected", "needs_confirmation"].includes(t.status) && <button className="btn" onClick={() => patch(t.id, { status: "pending" })}>Confirm</button>}
                    <select aria-label="Status" className="input w-auto py-1" value={t.status} onChange={(e) => patch(t.id, { status: e.target.value })}>{STATUSES.map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}</select>
                    <button className="btn" onClick={() => { setEdit(edit === t.id ? null : t.id); setEd({ assignee: t.assignee || "", due_date: t.due_date || "" }); }}>Edit</button>
                  </div>
                )}
              </div>
              {edit === t.id && (
                <div className="mt-3 flex flex-wrap items-end gap-3">
                  <div><label className="label">Owner</label><input className="input" value={ed.assignee} onChange={(e) => setEd({ ...ed, assignee: e.target.value })} /></div>
                  <div><label className="label">Due</label><input type="date" className="input" value={ed.due_date} onChange={(e) => setEd({ ...ed, due_date: e.target.value })} /></div>
                  <button className="btn btn-primary" onClick={() => { patch(t.id, { assignee: ed.assignee || undefined, clear_assignee: !ed.assignee, due_date: ed.due_date || undefined, clear_due: !ed.due_date }); setEdit(null); }}>Save</button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      <div className="mt-8"><Card title={`Open commitments (${commit?.total ?? 0})`}>
        {!commit?.items.length ? <p className="text-sm text-mute">No open commitments detected.</p> : <ul className="space-y-3 text-sm">{commit.items.map((c: any) => (
          <li key={c.id} className="flex flex-wrap items-center gap-3"><span className="min-w-0 flex-1"><b>{c.person || "Unknown speaker"}:</b> {c.text}{c.due_text && <span className="text-mute"> · {c.due_date || c.due_text}</span>}</span>
            <Link className="text-xs text-accent" href={`/conversations/${c.session_id}?t=${c.timestamp_ms ?? 0}`}>source</Link>
            {canWrite && <button className="btn" onClick={() => act(() => api(`/api/intelligence/commitments/${c.id}`, { method: "PATCH", json: { status: "fulfilled" } }))}>Mark fulfilled</button>}</li>))}</ul>}
      </Card></div>
    </>
  );
}
