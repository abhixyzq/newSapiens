"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { useApp } from "@/components/shell";
import { Badge, Empty, ErrorNote, PageHeader, Spinner } from "@/components/ui";

export default function Reports() {
  const { canWrite, ws } = useApp();
  const [rows, setRows] = useState<any[] | null>(null);
  const [kind, setKind] = useState("daily");
  const [day, setDay] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => api("/api/reports").then(setRows), []);
  useEffect(() => { load(); }, [load]);
  const gen = async () => {
    setBusy(true); setErr(null);
    try { const r = await api("/api/reports/generate", { json: { period_type: kind, anchor_date: day || undefined } }); location.href = `/reports/${r.id}`; }
    catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <>
      <PageHeader title="Reports" sub={ws.auto_daily_report && ws.type === "individual" ? `A draft daily report is generated automatically at ${ws.report_time} (${ws.timezone}). Review and approve it before sharing.` : "Generate, review, approve and share professional PDF reports."} />
      {canWrite && (
        <div className="card mb-6 flex flex-wrap items-end gap-3 p-4">
          <div><label className="label" htmlFor="k">Report</label><select id="k" className="input" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option>{ws.type !== "individual" && <option value="organization">Organization (shared content only)</option>}</select></div>
          <div><label className="label" htmlFor="d">Any day in the period</label><input id="d" type="date" className="input" value={day} onChange={(e) => setDay(e.target.value)} /></div>
          <button className="btn btn-primary" onClick={gen} disabled={busy}>{busy ? "Generating…" : "Generate report"}</button>
        </div>
      )}
      <ErrorNote error={err} />
      {!rows ? <Spinner /> : !rows.length ? <Empty title="No reports yet" hint="Generate one above once you have captured sessions." /> : (
        <div className="card divide-y divide-line">
          {rows.map((r) => (
            <Link key={r.id} href={`/reports/${r.id}`} className="flex flex-wrap items-center gap-3 px-4 py-3 hover:bg-bg">
              <div className="min-w-0 flex-1"><div className="truncate text-sm font-medium">{r.title}</div><div className="text-xs text-mute">Generated {fmtDateTime(r.generated_at)} · revision {r.revision}{r.auto_generated ? " · auto" : ""}</div></div>
              <Badge tone={r.status === "approved" ? "ok" : "warn"}>{r.status}</Badge>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
