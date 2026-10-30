"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtDur } from "@/lib/format";
import { Card, Empty, PageHeader, Spinner, Stat, StatusBadge } from "@/components/ui";

export default function Overview() {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const load = () => api("/api/dashboard/overview").then(setD).catch((e) => setErr(e.message));
    load();
    const t = setInterval(load, 15000); // pick up processing status changes
    return () => clearInterval(t);
  }, []);
  if (err) return <p className="text-bad">{err}</p>;
  if (!d) return <Spinner />;
  const s = d.stats, h = d.highlights;
  const none = !d.timeline.length;
  return (
    <>
      <PageHeader title="Today" sub={`${new Date(d.date + "T00:00:00").toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })} · ${d.timezone}`}
        actions={<Link href="/conversations?new=1" className="btn btn-primary">New session</Link>} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Captured sessions" value={s.sessions} />
        <Stat label="Meetings" value={s.meetings} />
        <Stat label="Conversation time" value={fmtDur(s.duration_seconds)} />
        <Stat label="Tasks identified" value={s.tasks_identified} hint="AI-detected" />
        <Stat label="Decisions identified" value={s.decisions_identified} hint="AI-detected" />
        <Stat label="Pending commitments" value={s.pending_commitments} />
        <Stat label="Open tasks" value={s.open_tasks} />
        <Stat label="Awaiting your review" value={s.needs_review} />
      </div>
      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <Card title="Activity timeline" className="lg:col-span-2">
          {none ? <Empty title="No sessions captured today" hint="Record a conversation, upload audio, or import a transcript to start building today’s intelligence." action={<Link href="/conversations?new=1" className="btn btn-primary">Start a session</Link>} /> : (
            <ol className="relative space-y-5 border-l border-line pl-5">
              {d.timeline.map((t: any) => (
                <li key={t.id} className="relative">
                  <span className="absolute -left-[25px] top-1.5 h-2.5 w-2.5 rounded-full bg-accent" />
                  <div className="flex flex-wrap items-center gap-2 text-xs text-mute"><span className="tabular-nums">{t.time}</span><span>·</span><span>{fmtDur(t.duration_seconds)}</span><StatusBadge status={t.status} /></div>
                  <Link href={`/conversations/${t.id}`} className="mt-0.5 block text-sm font-medium hover:text-accent">{t.title}</Link>
                  {t.participants.length > 0 && <div className="text-xs text-mute">{t.participants.join(", ")}</div>}
                  {t.summary && <p className="mt-1 text-sm text-mute">{t.summary}</p>}
                  {t.excluded ? <p className="mt-1 text-xs text-mute">Marked sensitive — not analysed.</p> : t.status === "ready" && <p className="mt-1 text-xs text-mute">{t.events.decisions || 0} decision(s) · {t.events.tasks || 0} task(s) detected</p>}
                </li>
              ))}
            </ol>
          )}
        </Card>
        <div className="space-y-6">
          <Card title="Recommendations">
            {d.recommendations.length ? <ul className="space-y-2 text-sm">{d.recommendations.map((r: any, i: number) => <li key={i}><Link className="hover:text-accent" href={r.link.split("?")[0]}>{r.text}</Link></li>)}</ul>
              : <p className="text-sm text-mute">Nothing needs attention based on your recorded data.</p>}
          </Card>
          <Card title="Approaching deadlines">
            {h.deadlines.length ? <ul className="space-y-2 text-sm">{h.deadlines.map((t: any) => <li key={t.id}><span className="font-medium">{t.due_date}</span> — {t.title}{t.assignee ? <span className="text-mute"> · {t.assignee}</span> : null}</li>)}</ul> : <p className="text-sm text-mute">No open tasks with a deadline in the next 3 days.</p>}
          </Card>
        </div>
      </div>
      <div className="mt-6 grid gap-6 md:grid-cols-2">
        <Card title="Decisions today">{h.decisions.length ? <ul className="space-y-2 text-sm">{h.decisions.map((x: any) => <li key={x.id}>{x.text}</li>)}</ul> : <p className="text-sm text-mute">None detected.</p>}</Card>
        <Card title="New tasks">{h.new_tasks.length ? <ul className="space-y-2 text-sm">{h.new_tasks.map((x: any) => <li key={x.id}>{x.title} <span className="text-mute">· {x.assignee || "no owner identified"}</span></li>)}</ul> : <p className="text-sm text-mute">None detected.</p>}</Card>
        <Card title="Unresolved questions (unverified)">{h.questions.length ? <ul className="space-y-2 text-sm">{h.questions.map((x: any) => <li key={x.id}>{x.text}</li>)}</ul> : <p className="text-sm text-mute">None detected.</p>}</Card>
        <Card title="Ideas">{h.ideas.length ? <ul className="space-y-2 text-sm">{h.ideas.map((x: any) => <li key={x.id}>{x.text}</li>)}</ul> : <p className="text-sm text-mute">None detected.</p>}</Card>
      </div>
    </>
  );
}
