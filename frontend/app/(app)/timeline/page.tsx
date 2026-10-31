"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import { fmtDur, fmtSpoken, shiftDate, withGaps } from "@/lib/format";
import { Spinner, StatusBadge } from "@/components/ui";

/** The phone app's Timeline: one day of events at the time they were spoken, with gaps where nothing was captured. */
export default function Timeline() {
  const [date, setDate] = useState<string | null>(null);
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setDate(new URLSearchParams(location.search).get("date")); }, []);
  useEffect(() => {
    const load = () => api(`/api/dashboard/overview${date ? `?date=${date}` : ""}`).then(setD).catch((e) => setErr(e.message));
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [date]);
  const go = (n: string) => { setD(null); setDate(n); history.replaceState(null, "", `?date=${n}`); };
  if (err) return <p className="text-bad">{err}</p>;
  if (!d) return <Spinner />;
  const today = new Date().toLocaleDateString("en-CA", { timeZone: d.timezone });
  const label = d.date === today ? "Today" : d.date === shiftDate(today, -1) ? "Yesterday"
    : new Date(d.date + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  const pending = d.timeline.filter((s: any) => !["ready", "failed", "excluded"].includes(s.status)).length;

  return (
    <div className="mx-auto max-w-2xl">
      <div className="flex items-center gap-1">
        <h1 className="flex-1 text-2xl font-bold tracking-tight">Timeline</h1>
        <button className="grid h-10 w-10 place-items-center rounded-full hover:bg-panel" aria-label="Previous day" onClick={() => go(shiftDate(d.date, -1))}><ChevronLeft size={20} /></button>
        <label className="relative cursor-pointer text-sm font-medium">
          {label}
          <input type="date" aria-label="Pick a day" max={today} className="absolute inset-0 cursor-pointer opacity-0" value={d.date} onChange={(e) => e.target.value && go(e.target.value)} />
        </label>
        <button className="grid h-10 w-10 place-items-center rounded-full hover:bg-panel disabled:opacity-30" aria-label="Next day" disabled={d.date >= today} onClick={() => go(shiftDate(d.date, 1))}><ChevronRight size={20} /></button>
      </div>
      {pending > 0 && <p className="text-sm text-mute">{pending} session(s) still processing…</p>}

      {d.events.length ? (
        <ol className="mt-3">
          {withGaps<any>(d.events).map((x, i) => "gapMin" in x ? (
            <li key={`g${i}`} className="py-2 pl-[5.5rem] text-xs text-mute">— {fmtDur(x.gapMin * 60)} with nothing captured —</li>
          ) : (
            <li key={x.event.id} className="appear">
              <Link href={`/conversations/${x.event.session_id}?t=${x.event.start_ms}`} className="flex gap-3 rounded-xl px-1 py-2.5 hover:bg-panel">
                <span className="w-[4.75rem] shrink-0 tabular-nums">
                  <span className="block text-sm font-semibold">{x.event.time}</span>
                  <span className="block text-xs text-mute">{x.event.end_time}</span>
                </span>
                <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-accent" />
                <span className="min-w-0 flex-1">
                  <span className="block font-semibold">{x.event.title}</span>
                  <span className="block text-xs capitalize text-mute">{x.event.category} · {fmtSpoken((Date.parse(x.event.end) - Date.parse(x.event.start)) / 1000)}</span>
                  {x.event.description && <span className="mt-0.5 block text-sm">{x.event.description}</span>}
                </span>
              </Link>
            </li>
          ))}
        </ol>
      ) : <p className="mt-6 text-mute">Nothing captured this day.</p>}

      {d.daily_summary?.summary && <div className="card mt-5 p-4"><div className="mb-1 text-sm font-bold">Summary</div><p className="text-sm leading-relaxed">{d.daily_summary.summary}</p></div>}

      {d.timeline.length > 0 && (
        <details className="mt-5 text-sm">
          <summary className="cursor-pointer text-mute">Sessions this day ({d.timeline.length})</summary>
          <ul className="mt-2 space-y-2">
            {d.timeline.map((t: any) => (
              <li key={t.id} className="flex items-center gap-2">
                <span className="tabular-nums text-mute">{t.time}</span>
                <Link href={`/conversations/${t.id}`} className="min-w-0 flex-1 truncate hover:text-accent">{t.title}</Link>
                <span className="text-xs text-mute">{fmtDur(t.duration_seconds)}</span>
                {t.status !== "ready" && <StatusBadge status={t.status} />}
              </li>
            ))}
          </ul>
        </details>
      )}
      <p className="mt-6 text-xs text-mute">Times are when you spoke, not when it was analysed. Titles are AI-written — open an event to read the transcript.</p>
    </div>
  );
}
