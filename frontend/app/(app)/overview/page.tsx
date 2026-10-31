"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AudioLines, ChevronDown, ChevronUp, Mic, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import { fmtDateTime, fmtDur, fmtSpoken } from "@/lib/format";
import { useApp } from "@/components/shell";
import { Spinner, StatusBadge } from "@/components/ui";

const greet = () => { const h = new Date().getHours(); return h >= 5 && h < 12 ? "Good morning" : h >= 12 && h < 17 ? "Good afternoon" : "Good evening"; };
const src = (sid: string | null, ms: number) => (sid ? `/conversations/${sid}?t=${ms}` : "/tasks");

function StatTile({ value, label, href }: { value: string | number; label: string; href: string }) {
  return (
    <Link href={href} className="card flex flex-col items-center py-3 hover:bg-bg">
      <span className="text-lg font-bold tabular-nums">{value}</span>
      <span className="text-[11px] text-mute">{label}</span>
    </Link>
  );
}

function Section({ title, link, href }: { title: string; link: string; href: string }) {
  return (
    <div className="mb-1 mt-7 flex items-center justify-between">
      <h2 className="text-base font-bold">{title}</h2>
      <Link href={href} className="text-sm font-medium text-accent">{link}</Link>
    </div>
  );
}

/** Home: the phone app's Home screen — today's overview, AI day summary, important moments, what to do, recent captures. */
export default function Overview() {
  const { me, canWrite } = useApp();
  const [d, setD] = useState<any>(null);
  const [recent, setRecent] = useState<any[]>([]);
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => Promise.all([api("/api/dashboard/overview").then(setD), api("/api/sessions?limit=3").then((r: any) => setRecent(r.items))])
    .catch((e) => setErr(e.message)), []);
  useEffect(() => {
    load();
    const t = setInterval(load, 15000); // new transcripts, moments and summary appear without a refresh
    return () => clearInterval(t);
  }, [load]);
  if (err) return <p className="text-bad">{err}</p>;
  if (!d) return <Spinner />;
  const s = d.stats, sum = d.daily_summary, t = d.tasks;
  const todo = [...t.unconfirmed, ...t.today].slice(0, 3);
  const done = (id: string) => api(`/api/tasks/${id}`, { method: "PATCH", json: { status: "completed" } }).then(load).catch((e) => setErr(e.message));

  return (
    <div className="mx-auto max-w-2xl">
      <div className="flex items-center gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2.5 text-sm font-medium text-mute">
            {new Date(d.date + "T00:00:00").toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}
            {d.listening && <span className="flex items-center gap-1.5 text-bad"><span className="rec-dot h-2 w-2 rounded-full bg-bad" />Listening</span>}
          </div>
          <h1 className="text-2xl font-bold tracking-tight">{greet()}, {me.name.split(" ")[0]}!</h1>
        </div>
        <Link href="/settings" aria-label="Settings" className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-accent text-lg font-bold text-white dark:text-[#1c1145]">{me.name[0]?.toUpperCase()}</Link>
      </div>

      <Link href="/ask" className="mt-4 flex items-center gap-3 rounded-full border border-line bg-panel px-5 py-3.5 text-mute hover:bg-bg">
        <Sparkles size={20} className="text-accent" /> Ask your memory…
      </Link>

      <div className="mt-4 grid grid-cols-4 gap-2">
        <StatTile value={fmtSpoken(d.speech_seconds)} label="Speech" href="/analytics" />
        <StatTile value={s.sessions} label="Sessions" href="/analytics" />
        <StatTile value={d.moments.length} label="Moments" href="/timeline" />
        <StatTile value={s.open_tasks} label="Pending" href="/tasks" />
      </div>

      <button className="card mt-4 block w-full p-4 text-left" onClick={() => setOpen(!open)} aria-expanded={open} disabled={!sum}>
        <div className="flex items-center gap-2">
          <Sparkles size={18} className="text-accent" />
          <span className="flex-1 text-sm font-bold">Your day at a glance</span>
          {sum && (open ? <ChevronUp size={18} className="text-mute" /> : <ChevronDown size={18} className="text-mute" />)}
        </div>
        <p className={`mt-2 text-[15px] leading-relaxed ${sum ? "" : "text-mute"}`}>
          {sum?.summary || (sum?.highlights.length ? `Main events: ${sum.highlights.join("; ")}.`
            : s.sessions ? "Your summary appears once today's conversations finish processing." : "Nothing captured yet today. Tap Capture to start.")}
        </p>
        {open && sum && (
          <div className="appear">
            {sum.summary && <ul className="mt-2 space-y-1 text-sm">{sum.highlights.map((h: string, i: number) => <li key={i}>•&nbsp; {h}</li>)}</ul>}
            <p className="mt-2 text-xs text-mute">Updated {fmtDateTime(sum.generated_at)} from today's transcripts · AI-written; “Likely” marks inferences</p>
          </div>
        )}
      </button>

      <Section title="Important moments" link="Timeline" href="/timeline" />
      {d.moments.length ? (
        <ol>
          {d.moments.slice(-6).map((m: any) => (
            <li key={m.id} className="appear">
              <Link href={src(m.session_id, m.start_ms)} className="flex gap-3 rounded-xl px-1 py-2.5 hover:bg-panel">
                <span className="w-20 shrink-0 pt-0.5 text-xs tabular-nums text-mute">{m.time}</span>
                <span className="min-w-0">
                  <span className="block text-sm font-semibold text-accent">{m.title}</span>
                  <span className="line-clamp-2 text-[15px]">{m.text}</span>
                </span>
              </Link>
            </li>
          ))}
        </ol>
      ) : <p className="py-1.5 text-mute">Decisions, tasks, commitments and ideas from today show up here at the time they were said.</p>}

      <Section title="What to do" link="All tasks" href="/tasks" />
      {!todo.length && <p className="text-mute">Nothing open.</p>}
      {todo.map((x: any) => (
        <div key={x.id} className="flex items-start gap-3 py-2">
          <input type="checkbox" aria-label={`Mark “${x.title}” done`} className="mt-1 h-5 w-5 accent-[rgb(var(--accent))]" disabled={!canWrite} onChange={() => done(x.id)} />
          <Link href={src(x.session_id, x.start_ms)} className="min-w-0 flex-1">
            <span className="block text-[15px] font-medium">{x.title}</span>
            {x.due_text && !x.due_date ? <span className="text-sm text-bad">Confirm date: “{x.due_text}”</span>
              : x.due_date && <span className="text-sm text-bad">Due: {new Date(x.due_date + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" })}</span>}
          </Link>
        </div>
      ))}
      {t.upcoming.length > 0 && <Link href="/tasks" className="mt-1 block text-sm text-mute">Upcoming: {t.upcoming.slice(0, 2).map((x: any) => `${x.title} (${new Date(x.due_date + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" })})`).join(" · ")}</Link>}
      {d.recommendations.length > 0 && <div className="mt-3 space-y-1 text-sm">{d.recommendations.map((r: any, i: number) => <Link key={i} className="block text-warn" href={r.link.split("?")[0]}>{r.text}</Link>)}</div>}

      <Section title="Recent captures" link="View all" href="/conversations" />
      {!recent.length && <p className="py-2 text-mute">Tap Capture below — captures appear here.</p>}
      {recent.map((r) => (
        <Link key={r.id} href={`/conversations/${r.id}`} className="flex items-center gap-3 rounded-xl px-1 py-2.5 hover:bg-panel">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-soft text-accent"><AudioLines size={20} /></span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-semibold">{r.title}</span>
            <span className="text-xs text-mute">{fmtDateTime(r.occurred_at)} · {fmtDur(r.duration_seconds)}</span>
          </span>
          {r.status !== "ready" && <StatusBadge status={r.status} />}
        </Link>
      ))}
      {canWrite && (  /* floating pill, pinned to the bottom of the scroll area like the phone's */
        <div className="pointer-events-none sticky bottom-4 mt-8 flex justify-center">
          <Link href="/conversations?new=1" className="pointer-events-auto flex h-14 items-center gap-2 rounded-full bg-navy px-7 font-semibold text-bg shadow-xl">
            {d.listening ? <><span className="rec-dot h-2.5 w-2.5 rounded-full bg-bad" /> Recording</> : <><Mic size={20} /> Capture</>}
          </Link>
        </div>
      )}
    </div>
  );
}
