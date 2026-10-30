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
