"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { useApp } from "@/components/shell";
import { Card, ErrorNote, PageHeader } from "@/components/ui";

export default function Settings() {
  const { me, ws, refresh } = useApp();
  const [p, setP] = useState({ name: me.name, timezone: me.timezone, ret: me.audio_retention_days?.toString() || "" });
  const [w, setW] = useState({ report_time: ws.report_time, timezone: ws.timezone, auto: ws.auto_daily_report });
  const [pw, setPw] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const run = async (fn: () => Promise<any>, ok: string) => { setErr(null); setMsg(null); try { await fn(); await refresh(); setMsg(ok); } catch (e: any) { setErr(e.message); } };
  const canWs = ["owner", "admin"].includes(ws.role);
  const tzs = (Intl as any).supportedValuesOf ? (Intl as any).supportedValuesOf("timeZone") as string[] : [me.timezone];
  return (
    <>
      <PageHeader title="Settings" />
      <ErrorNote error={err} />{msg && <p role="status" className="mb-4 rounded-lg border border-ok/30 bg-ok/10 px-3 py-2 text-sm text-ok">{msg}</p>}
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Profile & privacy">
          <div className="space-y-3">
            <div><label className="label" htmlFor="sn">Name</label><input id="sn" className="input" value={p.name} onChange={(e) => setP({ ...p, name: e.target.value })} /></div>
            <div><label className="label" htmlFor="st">Timezone (used to interpret “yesterday”, “25 January”, report days)</label><select id="st" className="input" value={p.timezone} onChange={(e) => setP({ ...p, timezone: e.target.value })}>{tzs.map((t) => <option key={t}>{t}</option>)}</select></div>
            <div><label className="label" htmlFor="sr">Delete original audio after (days) — blank keeps audio until you delete it</label><input id="sr" type="number" min={1} className="input" value={p.ret} onChange={(e) => setP({ ...p, ret: e.target.value })} />
              <p className="mt-1 text-xs text-mute">Applies to audio uploaded from now on. Transcripts are kept until you delete them.</p></div>
            <button className="btn btn-primary" onClick={() => run(() => api("/api/auth/me", { method: "PATCH", json: { name: p.name, timezone: p.timezone, audio_retention_days: p.ret ? +p.ret : undefined, clear_retention: !p.ret } }), "Saved")}>Save</button>
          </div>
        </Card>
        <Card title="Transcription languages">
          <p className="text-sm text-mute">English and Hindi, including Hinglish. Hindi is written in Devanagari, with an English translation alongside.</p>
        </Card>
        <Card title={`Workspace reports — ${ws.name}`}>
          {!canWs ? <p className="text-sm text-mute">Only workspace owners/admins can change these.</p> : (
            <div className="space-y-3">
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={w.auto} onChange={(e) => setW({ ...w, auto: e.target.checked })} /> Generate a draft daily report automatically</label>
              <div><label className="label" htmlFor="rt">Report time</label><input id="rt" type="time" className="input" value={w.report_time} onChange={(e) => setW({ ...w, report_time: e.target.value })} /></div>
              <div><label className="label" htmlFor="rz">Report timezone</label><select id="rz" className="input" value={w.timezone} onChange={(e) => setW({ ...w, timezone: e.target.value })}>{tzs.map((t) => <option key={t}>{t}</option>)}</select></div>
              <button className="btn btn-primary" onClick={() => run(() => api(`/api/workspaces/${ws.id}`, { method: "PATCH", json: { report_time: w.report_time, timezone: w.timezone, auto_daily_report: w.auto } }), "Saved")}>Save</button>
            </div>
          )}
        </Card>
        <Card title="Your data">
          <p className="mb-3 text-sm text-mute">Download everything you own (sessions, transcripts, decisions, tasks) as JSON.</p>
          <a className="btn" href="/api/auth/export" download="newsapiens-export.json">Export my data</a>
        </Card>
        <Card title="Delete account">
          <p className="mb-3 text-sm text-mute">Permanently deletes your account, personal workspace, recordings (audio included), transcripts and derived data. This cannot be undone.</p>
          <div className="flex gap-2"><input type="password" className="input" aria-label="Password" placeholder="Confirm password" value={pw} onChange={(e) => setPw(e.target.value)} />
            <button className="btn btn-danger" onClick={() => confirm("Permanently delete your account and all data?") && run(async () => { await api("/api/auth/me", { method: "DELETE", json: { password: pw } }); location.href = "/register"; }, "Deleted")}>Delete</button></div>
        </Card>
      </div>
    </>
  );
}
