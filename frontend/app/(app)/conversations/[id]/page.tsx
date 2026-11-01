"use client";
import { Suspense, use, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Pencil, RefreshCw, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { fmtDateTime, fmtDur, fmtTs } from "@/lib/format";
import { useApp } from "@/components/shell";
import { Badge, Card, Empty, ErrorNote, PageHeader, Segmented, Spinner, StatusBadge } from "@/components/ui";

function Detail({ id }: { id: string }) {
  const { me, ws } = useApp();
  const router = useRouter();
  const t0 = useSearchParams().get("t");
  const [s, setS] = useState<any>(null);
  const [segs, setSegs] = useState<any[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [audio, setAudio] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [view, setView] = useState<"en" | "orig">("en");
  const player = useRef<HTMLAudioElement>(null);

  const load = useCallback(async () => {
    try {
      const d = await api(`/api/sessions/${id}`);
      setS(d);
      if (d.segments) setSegs((await api(`/api/sessions/${id}/transcript`)).segments);
    } catch (e: any) { setErr(e.message); }
  }, [id]);
  useEffect(() => {
    load();
    const t = setInterval(() => { if (["uploaded", "transcribing", "analyzing", "recording"].includes(s?.status)) load(); }, 4000);
    return () => clearInterval(t);
  }, [load, s?.status]);
  useEffect(() => { if (s?.audio_chunks) api(`/api/sessions/${id}/audio-url`).then((r) => setAudio(r.url)).catch(() => setAudio(null)); }, [id, s?.audio_chunks, s?.status]);
  useEffect(() => { if (t0 && segs.length) document.getElementById(`ms-${segs.find((x) => x.start_ms >= +t0)?.idx ?? segs[segs.length - 1].idx}`)?.scrollIntoView({ block: "center" }); }, [t0, segs]);

  if (err) return <p className="text-bad">{err}</p>;
  if (!s) return <Spinner />;
  const owner = s.owner_id === me.id;
  const seek = (ms: number) => { if (player.current) { player.current.currentTime = ms / 1000; player.current.play(); } };
  const act = async (fn: () => Promise<any>) => { setErr(null); try { await fn(); await load(); } catch (e: any) { setErr(e.message); } };
  const review = (kind: string, rid: string, body: any) => act(() => api(`/api/${kind}/${rid}`, { method: "PATCH", json: body }));
  const orig = view === "orig";
  const noSource = orig && !segs.some((g) => g.source_text);

  return (
    <>
      <PageHeader title={s.title} sub={`${fmtDateTime(s.occurred_at)} · ${s.kind.replace("_", " ")} · ${fmtDur(s.duration_seconds)} · ${s.visibility}`}
        actions={<><StatusBadge status={s.status} />
          {owner && s.status !== "excluded" && <button className="btn" onClick={() => act(() => api(`/api/intelligence/process/${id}`, { method: "POST" }))}><RefreshCw size={14} /> Reprocess</button>}
          {owner && <button className="btn btn-danger" onClick={async () => {
            const all = confirm("Delete this recording AND its transcript, tasks and decisions?\n\nOK = delete everything\nCancel = choose audio-only");
            if (all) { await api(`/api/sessions/${id}?mode=everything`, { method: "DELETE" }); router.push("/conversations"); }
            else if (s.audio_chunks && confirm("Delete only the audio file (transcript and extracted items are kept)?")) act(() => api(`/api/sessions/${id}?mode=audio`, { method: "DELETE" }));
          }}><Trash2 size={14} /> Delete</button>}</>} />
      <ErrorNote error={err} />
      {s.error && <p className="mb-4 rounded-lg border border-warn/30 bg-warn/10 px-3 py-2 text-sm text-warn">{s.error}</p>}
      {s.status === "excluded" && <Empty title="Marked sensitive" hint="This session is stored but not transcribed, analysed, searched or included in reports." action={owner && <button className="btn" onClick={() => act(() => api(`/api/sessions/${id}`, { method: "PATCH", json: { exclude_from_processing: false } }))}>Remove exclusion</button>} />}
      {audio && <audio ref={player} className="mb-4 w-full" controls src={audio} />}
      {s.summary && (
        <Card title="Summary" className="mb-6" right={<span className="text-xs text-mute">{s.summary.generated_by}</span>}>
          <p className="text-sm leading-relaxed">{s.summary.executive_summary || "—"}</p>
          {s.summary.topics?.length > 0 && <div className="mt-3 flex flex-wrap gap-1.5">{s.summary.topics.map((t: string) => <Badge key={t} tone="info">{t}</Badge>)}</div>}
        </Card>
      )}
      {s.status === "ready" && (
        <div className="mb-6 grid gap-6 md:grid-cols-2">
          <Card title={`Decisions (${s.decisions.length})`}>
            {!s.decisions.length ? <p className="text-sm text-mute">No decisions detected.</p> : <ul className="space-y-3 text-sm">{s.decisions.map((d: any) => (
              <li key={d.id}><button className="text-left hover:text-accent" onClick={() => seek(d.timestamp_ms)}>{d.text}</button>
                <div className="mt-1 flex items-center gap-2 text-xs text-mute"><span>@{fmtTs(d.timestamp_ms)}</span>
                  {d.status === "confirmed" ? <Badge tone="ok">Confirmed</Badge> : <Badge tone="warn">Needs review · {Math.round(d.confidence * 100)}%</Badge>}
                  {owner && d.status !== "confirmed" && <button className="text-accent" onClick={() => review("intelligence/decisions", d.id, { status: "confirmed" })}>Confirm</button>}
                  {owner && <button className="text-bad" onClick={() => review("intelligence/decisions", d.id, { status: "rejected" })}>Reject</button>}</div></li>))}</ul>}
          </Card>
          <Card title={`Tasks (${s.tasks.length})`}>
            {!s.tasks.length ? <p className="text-sm text-mute">No tasks detected.</p> : <ul className="space-y-3 text-sm">{s.tasks.map((t: any) => (
              <li key={t.id}>{t.title}<div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-mute">
                <span>{t.assignee ? `Assigned to ${t.assignee}` : "No owner identified"}</span>{(t.due_date || t.due_text) && <span>· due {t.due_date || `“${t.due_text}”`}</span>}
                <StatusBadge status={t.status} /><span>@{fmtTs(t.timestamp_ms)}</span>
                {t.status !== "pending" && t.status !== "completed" && <button className="text-accent" onClick={() => review("tasks", t.id, { status: "pending" })}>Confirm</button>}</div></li>))}</ul>}
          </Card>
          <Card title={`Commitments (${s.commitments.length})`}>
            {!s.commitments.length ? <p className="text-sm text-mute">None detected.</p> : <ul className="space-y-2 text-sm">{s.commitments.map((c: any) => <li key={c.id}><b>{c.person || "Unknown speaker"}:</b> {c.text} <span className="text-xs text-mute">@{fmtTs(c.timestamp_ms)}</span></li>)}</ul>}
          </Card>
          <Card title="Risks, ideas & open questions">
            {!s.insights.length ? <p className="text-sm text-mute">None detected.</p> : <ul className="space-y-2 text-sm">{s.insights.map((i: any) => <li key={i.id}><Badge tone={i.kind === "risk" ? "bad" : i.kind === "idea" ? "ok" : "warn"}>{i.kind}</Badge> {i.text}</li>)}</ul>}
          </Card>
        </div>
      )}
      <Card title={`Transcript (${segs.length})`}>
        {segs.length > 0 && <Segmented className="mb-3" options={[["en", "Translated"], ["orig", `Original${s.language ? ` (${s.language})` : ""}`]] as const} value={view} onChange={setView} />}
        {noSource && <p className="mb-3 text-sm text-mute">No separate original-language text for this recording; showing the English text.
          {owner && audio && <button className="ml-1 text-accent" onClick={() => act(() => api(`/api/intelligence/process/${id}?retranscribe=true`, { method: "POST" }))}>Re-transcribe to fetch it</button>}</p>}
        {!segs.length ? <p className="text-sm text-mute">{["transcribing", "analyzing", "uploaded"].includes(s.status) ? "Processing… this page refreshes automatically." : "No transcript yet."}</p> : (
          <ol className="space-y-1">
            {segs.map((g) => (
              <li key={g.id} id={`ms-${g.idx}`} className={`group flex gap-3 rounded-lg px-2 py-1.5 text-sm ${t0 && Math.abs(g.start_ms - +t0) < 1500 ? "bg-accent/10" : "hover:bg-bg"}`}>
                <button className="w-12 shrink-0 text-right text-xs tabular-nums text-mute hover:text-accent" onClick={() => seek(g.start_ms)}>{fmtTs(g.start_ms)}</button>
                <div className="min-w-0 flex-1">
                  {g.speaker && <span className="mr-2 font-medium">{g.speaker}</span>}
                  {orig ? <span>{g.source_text || g.text}</span> : editing === g.id ? (
                    <span className="flex gap-2"><textarea className="input" rows={2} value={draft} onChange={(e) => setDraft(e.target.value)} />
                      <button className="btn btn-primary" onClick={() => act(async () => { await api(`/api/transcripts/${g.id}`, { method: "PATCH", json: { text: draft } }); setEditing(null); })}>Save</button>
                      <button className="btn" onClick={() => setEditing(null)}>Cancel</button></span>
                  ) : <span>{g.text}</span>}
                  {g.edited && <span title={`Original: ${g.original_text}`} className="ml-2 text-xs text-mute">(edited)</span>}
                  {g.confidence != null && g.confidence < 0.6 && <span className="ml-2 text-xs text-warn">low confidence</span>}
                </div>
                {owner && !orig && editing !== g.id && <button className="invisible text-mute hover:text-accent group-hover:visible" aria-label="Edit line" onClick={() => { setEditing(g.id); setDraft(g.text); }}><Pencil size={14} /></button>}
              </li>
            ))}
          </ol>
        )}
      </Card>
    </>
  );
}

export default function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <Suspense><Detail id={id} /></Suspense>;
}
