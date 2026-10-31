"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtDur } from "@/lib/format";
import { Card, Empty, PageHeader, Spinner, Stat } from "@/components/ui";

export default function Analytics() {
  const [days, setDays] = useState(30);
  const [a, setA] = useState<any>(null);
  useEffect(() => { setA(null); api(`/api/analytics?days=${days}`).then(setA); }, [days]);
  if (!a) return <Spinner />;
  const max = Math.max(1, ...a.per_day.map((d: any) => d.duration_seconds));
  return (
    <>
      <PageHeader title="Insights" sub={a.note} actions={<select aria-label="Range" className="input w-auto" value={days} onChange={(e) => setDays(+e.target.value)}><option value={7}>7 days</option><option value={30}>30 days</option><option value={90}>90 days</option></select>} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Tasks created" value={a.tasks.created_in_range} hint="AI-detected + manual" />
        <Stat label="Tasks completed" value={a.tasks.completed_in_range} />
        <Stat label="Open tasks now" value={a.tasks.open_now} />
        <Stat label="Open commitments" value={a.open_commitments} />
      </div>
      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <Card title="Recorded time per day (observed)" className="lg:col-span-2">
          {!a.per_day.length ? <Empty title="No recordings in this range" /> : (
            <div className="flex h-44 items-end gap-1" role="img" aria-label="Recorded time per day">
              {a.per_day.map((d: any) => <div key={d.date} title={`${d.date}: ${fmtDur(d.duration_seconds)}, ${d.sessions} session(s)`} className="flex-1 rounded-t bg-accent/70 hover:bg-accent" style={{ height: `${Math.max(4, (d.duration_seconds / max) * 100)}%` }} />)}
            </div>
          )}
        </Card>
        <Card title="Time by session type">
          {!a.by_kind.length ? <p className="text-sm text-mute">No data.</p> : <ul className="space-y-2 text-sm">{a.by_kind.map((k: any) => <li key={k.kind} className="flex justify-between"><span>{k.kind.replace("_", " ")}</span><span className="tabular-nums text-mute">{k.sessions} · {fmtDur(k.duration_seconds)}</span></li>)}</ul>}
        </Card>
        <Card title="Tasks by status">{Object.keys(a.tasks.by_status).length ? <ul className="space-y-2 text-sm">{Object.entries(a.tasks.by_status).map(([k, v]: any) => <li key={k} className="flex justify-between"><span>{k.replace("_", " ")}</span><span className="tabular-nums text-mute">{v}</span></li>)}</ul> : <p className="text-sm text-mute">No tasks.</p>}</Card>
        <Card title="Decisions detected"><div className="text-3xl font-semibold tabular-nums">{a.decisions_in_range}</div><p className="mt-1 text-xs text-mute">AI-detected in the selected range; not verified.</p></Card>
      </div>
    </>
  );
}
