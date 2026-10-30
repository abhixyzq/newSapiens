"use client";
import { ReactNode } from "react";
import { STATUS_LABEL } from "@/lib/format";

export function PageHeader({ title, sub, actions }: { title: string; sub?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {sub && <p className="mt-1 text-sm text-mute">{sub}</p>}
      </div>
      <div className="flex items-center gap-2">{actions}</div>
    </div>
  );
}

export function Card({ title, right, children, className = "" }: { title?: string; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card p-5 ${className}`}>
      {(title || right) && (
        <div className="mb-3 flex items-center justify-between">
          {title && <h2 className="h-section">{title}</h2>}
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="card p-4">
      <div className="text-xs text-mute">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-mute">{hint}</div>}
    </div>
  );
}

export function Empty({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line px-6 py-10 text-center">
      <p className="text-sm font-medium">{title}</p>
      {hint && <p className="mx-auto mt-1 max-w-md text-sm text-mute">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

const TONE: Record<string, string> = {
  ok: "bg-ok/10 text-ok", warn: "bg-warn/10 text-warn", bad: "bg-bad/10 text-bad", info: "bg-accent/10 text-accent", mute: "bg-line/60 text-mute",
};
export function Badge({ tone = "mute", children }: { tone?: keyof typeof TONE; children: ReactNode }) {
  return <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-[11px] font-medium ${TONE[tone]}`}>{children}</span>;
}

export function StatusBadge({ status }: { status: string }) {
  const tone = ["ready", "completed"].includes(status) ? "ok" : ["failed", "cancelled"].includes(status) ? "bad"
    : ["needs_confirmation", "awaiting_transcription", "excluded", "detected"].includes(status) ? "warn"
    : ["transcribing", "analyzing", "recording", "uploaded", "in_progress"].includes(status) ? "info" : "mute";
  return <Badge tone={tone as any}>{STATUS_LABEL[status] || status}</Badge>;
}

/** Trust label used everywhere AI output appears. */
export function ConfBadge({ label }: { label: string }) {
  const m: Record<string, [string, any]> = { observed: ["Observed", "mute"], confirmed: ["Confirmed", "ok"], ai: ["AI-detected", "info"], uncertain: ["Needs review", "warn"] };
  const [t, tone] = m[label] || [label, "mute"];
  return <Badge tone={tone}>{t}</Badge>;
}

export function Spinner() {
  return <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-line border-t-accent" role="status" aria-label="Loading" />;
}

export function ErrorNote({ error }: { error?: string | null }) {
  return error ? <p role="alert" className="rounded-lg border border-bad/30 bg-bad/10 px-3 py-2 text-sm text-bad">{error}</p> : null;
}
