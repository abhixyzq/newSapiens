export const fmtDur = (s?: number | null) => {
  if (!s) return "—";
  const m = Math.round(s / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${Math.max(m, 1)}m`;
};
export const fmtTs = (ms?: number | null) => {
  const t = Math.floor((ms || 0) / 1000);
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(s).padStart(2, "0")}`;
};
export const fmtDateTime = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
export const fmtDate = (iso?: string | null) =>
  iso ? new Date(iso.length === 10 ? iso + "T00:00:00" : iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric", weekday: "short" }) : "—";

export const STATUS_LABEL: Record<string, string> = {
  created: "Created", recording: "Recording", paused: "Paused", uploaded: "Queued", transcribing: "Transcribing", analyzing: "Analyzing",
  ready: "Ready", failed: "Failed", excluded: "Excluded (sensitive)", awaiting_transcription: "Awaiting transcript", deleted: "Deleted",
  detected: "Detected", needs_confirmation: "Needs confirmation", pending: "Pending", in_progress: "In progress", completed: "Completed", cancelled: "Cancelled",
};

/** Events in order, with a gap marker wherever nothing was captured for at least `minGapMin` minutes. Never fills the gap. */
export function withGaps<T extends { start: string; end: string }>(events: T[], minGapMin = 15): ({ event: T } | { gapMin: number })[] {
  const out: ({ event: T } | { gapMin: number })[] = [];
  let lastEnd = 0;
  for (const e of events) {
    const s = Date.parse(e.start);
    const gap = Math.round((s - lastEnd) / 60000);
    if (lastEnd && gap >= minGapMin) out.push({ gapMin: gap });
    out.push({ event: e });
    lastEnd = Math.max(lastEnd, Date.parse(e.end));
  }
  return out;
}

/** "2026-10-31" + n days, as YYYY-MM-DD (calendar math, no timezone drift). */
export const shiftDate = (d: string, n: number) => {
  const t = new Date(d + "T00:00:00Z");
  t.setUTCDate(t.getUTCDate() + n);
  return t.toISOString().slice(0, 10);
};

/** Speech total like the phone's overview tile: "45s", "12m", "4h 20m". */
export const fmtSpoken = (sec?: number | null) => {
  const s = Math.floor(sec || 0);
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m` : `${Math.floor(s / 3600)}h ${Math.floor(s / 60) % 60}m`;
};
