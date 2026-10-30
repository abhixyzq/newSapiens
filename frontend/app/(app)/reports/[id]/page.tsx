"use client";
import { use, useCallback, useEffect, useState } from "react";
import { Download, RefreshCw, Trash2 } from "lucide-react";
import { api, download } from "@/lib/api";
import { useApp } from "@/components/shell";
import { Badge, Card, ConfBadge, ErrorNote, PageHeader, Spinner } from "@/components/ui";

const TITLES: Record<string, string> = { executive_summary: "Executive summary", timeline: "Workday timeline", meetings: "Meetings and discussions", decisions: "Important decisions", tasks: "Tasks and responsibilities", commitments: "Pending commitments", risks: "Risks and blockers", ideas: "Ideas and opportunities", questions: "Open questions (unverified)", ai_insights: "AI insights", priorities: "Tomorrow’s priorities" };

export default function Report({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { me } = useApp();
  const [r, setR] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [shareIds, setShareIds] = useState("");
  const load = useCallback(() => api(`/api/reports/${id}`).then((x) => { setR(x); setNote(x.content.notes || ""); }).catch((e) => setErr(e.message)), [id]);
  useEffect(() => { load(); }, [load]);
  if (!r) return err ? <p className="text-bad">{err}</p> : <Spinner />;
  const mine = r.owner_id === me.id;
  const c = r.content;
  const act = async (fn: () => Promise<any>) => { setErr(null); try { await fn(); await load(); } catch (e: any) { setErr(e.message); } };
  return (
    <>
      <PageHeader title={r.title} sub={`${c.period.label} · ${c.timezone} · revision ${r.revision}`}
        actions={<><Badge tone={r.status === "approved" ? "ok" : "warn"}>{r.status === "approved" ? "Approved" : "Draft — not yet reviewed"}</Badge>
          <button className="btn" onClick={() => download(`/api/reports/${id}/download`, `newsapiens-${r.period_type}-${r.period_start}.pdf`)}><Download size={14} /> PDF</button>
          {mine && <button className="btn" onClick={() => act(() => api("/api/reports/generate", { json: { period_type: r.period_type, anchor_date: r.period_start } }))}><RefreshCw size={14} /> Regenerate</button>}
          {mine && r.status !== "approved" && <button className="btn btn-primary" onClick={() => act(() => api(`/api/reports/${id}/approve`, { method: "POST" }))}>Approve</button>}</>} />
      <ErrorNote error={err} />
      {mine && (
        <Card className="mb-6" title="Author’s note">
          <textarea className="input" rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Add context for readers (appears at the top of the PDF)" />
          <button className="btn mt-2" onClick={() => act(() => api(`/api/reports/${id}`, { method: "PATCH", json: { notes: note } }))}>Save note</button>
          <p className="mt-2 text-xs text-mute">Editing returns the report to draft. Use the trash icon on any item you judge wrong to drop it from the report.</p>
        </Card>
      )}
      <div className="space-y-6">
        {Object.entries(c.sections).map(([k, items]: any) => (
          <Card key={k} title={TITLES[k] || k}>
            <ul className="space-y-3 text-sm">
              {items.map((it: any) => (
                <li key={it.id} className="flex gap-3">
                  <div className="min-w-0 flex-1">
                    {k === "timeline" ? <><b>{it.time}</b> · {it.title} <span className="text-mute">({it.kind}, {it.duration}{it.participants?.length ? `, ${it.participants.join(", ")}` : ""})</span>{it.summary && <p className="text-mute">{it.summary}</p>}</>
                      : k === "meetings" ? <><b>{it.title}</b> <span className="text-mute">{it.when}</span><p>{it.summary}</p><p className="text-xs text-mute">Method: {it.summary_method}</p></>
                      : <>{it.text}{it.owner && <span className="text-mute"> — {it.owner}</span>}{it.person && <span className="text-mute"> — {it.person}</span>}{it.due && <span className="text-mute"> · due {it.due}</span>}{it.reason && <span className="text-mute"> ({it.reason})</span>}
                        {it.ref && <span className="ml-1 text-xs text-accent">[{it.ref}]</span>}</>}
                  </div>
                  <ConfBadge label={it.label} />
                  {mine && <button className="text-mute hover:text-bad" aria-label="Remove item" onClick={() => act(() => api(`/api/reports/${id}`, { method: "PATCH", json: { remove_items: [it.id] } }))}><Trash2 size={14} /></button>}
                </li>
              ))}
            </ul>
          </Card>
        ))}
        <Card title="Appendix — sources and confidence notes">
          <ul className="mb-4 list-disc space-y-1 pl-5 text-xs text-mute">{c.appendix.confidence_notes.map((n: string) => <li key={n}>{n}</li>)}</ul>
          <ul className="space-y-2 text-xs">{c.appendix.sources.map((s: any) => <li key={s.ref}><b className="text-accent">{s.ref}</b> {s.session} · {s.when} · @{Math.floor(s.start_ms / 60000)}:{String(Math.floor(s.start_ms / 1000) % 60).padStart(2, "0")}<p className="text-mute">{s.excerpt || "(no excerpt)"}</p></li>)}</ul>
        </Card>
        {mine && r.status === "approved" && (
          <Card title="Share">
            <p className="mb-2 text-xs text-mute">Recipients must be members of this workspace. Sharing a report does not give them access to the underlying recordings. Currently shared with {r.shared_with.length} user(s).</p>
            <div className="flex gap-2"><input className="input" placeholder="User IDs, comma separated" value={shareIds} onChange={(e) => setShareIds(e.target.value)} />
              <button className="btn" onClick={() => act(() => api(`/api/reports/${id}/share`, { json: { user_ids: shareIds.split(",").map((x) => x.trim()).filter(Boolean) } }))}>Share</button></div>
          </Card>
        )}
      </div>
    </>
  );
}
