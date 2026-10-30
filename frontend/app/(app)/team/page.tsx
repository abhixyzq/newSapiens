"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { useApp } from "@/components/shell";
import { Badge, Card, Empty, ErrorNote, PageHeader } from "@/components/ui";

const ROLES = ["admin", "manager", "member", "viewer"];

export default function Team() {
  const { me, refresh } = useApp();
  const [orgs, setOrgs] = useState<any[]>([]);
  const [org, setOrg] = useState<any>(null);
  const [members, setMembers] = useState<any[]>([]);
  const [audit, setAudit] = useState<any[]>([]);
  const [name, setName] = useState("");
  const [inv, setInv] = useState({ email: "", role: "member" });
  const [token, setToken] = useState<string | null>(null);
  const [accept, setAccept] = useState("");
  const [team, setTeam] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const loadOrgs = useCallback(async () => { const o = await api("/api/organizations"); setOrgs(o); setOrg((cur: any) => o.find((x: any) => x.id === cur?.id) || o[0] || null); }, []);
  useEffect(() => { loadOrgs(); }, [loadOrgs]);
  useEffect(() => {
    if (!org) return;
    api(`/api/organizations/${org.id}/members`).then(setMembers);
    if (["owner", "admin"].includes(org.role)) api(`/api/organizations/${org.id}/audit?limit=30`).then(setAudit).catch(() => setAudit([])); else setAudit([]);
  }, [org]);
  const act = async (fn: () => Promise<any>) => { setErr(null); try { await fn(); } catch (e: any) { setErr(e.message); } };
  const admin = org && ["owner", "admin"].includes(org.role);

  return (
    <>
      <PageHeader title="Team Workspace" sub="Organization membership never grants access to anyone’s private recordings. Content is visible only where its owner shared it: privately, with a team, or with the whole organization." />
      <ErrorNote error={err} />
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-4">
          <Card title="Create organization"><div className="flex gap-2"><input className="input" aria-label="Organization name" placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} />
            <button className="btn btn-primary" onClick={() => act(async () => { await api("/api/organizations", { json: { name } }); setName(""); await refresh(); await loadOrgs(); })}>Create</button></div></Card>
          <Card title="Accept an invitation"><div className="flex gap-2"><input className="input" aria-label="Invitation token" placeholder="Invitation token" value={accept} onChange={(e) => setAccept(e.target.value)} />
            <button className="btn" onClick={() => act(async () => { await api("/api/invitations/accept", { json: { token: accept } }); setAccept(""); await refresh(); await loadOrgs(); })}>Join</button></div></Card>
          {orgs.length > 0 && <div className="card divide-y divide-line">{orgs.map((o) => <button key={o.id} onClick={() => setOrg(o)} className={`block w-full px-4 py-3 text-left text-sm hover:bg-bg ${org?.id === o.id ? "bg-bg" : ""}`}>{o.name} <Badge>{o.role}</Badge></button>)}</div>}
        </div>
        <div className="space-y-6 lg:col-span-2">
          {!org ? <Empty title="You’re not in an organization" hint="Create one, or paste an invitation token. Switch workspaces from the top bar." /> : (
            <>
              <Card title={`${org.name} — members (${members.length})`}>
                <ul className="divide-y divide-line text-sm">{members.map((m) => (
                  <li key={m.user_id} className="flex flex-wrap items-center gap-3 py-2"><span className="min-w-0 flex-1">{m.name} <span className="text-xs text-mute">{m.email}</span></span>
                    {admin && m.role !== "owner" && m.user_id !== me.id ? (<>
                      <select aria-label={`Role of ${m.name}`} className="input w-auto py-1" value={m.role} onChange={(e) => act(async () => { await api(`/api/organizations/${org.id}/members/${m.user_id}`, { method: "PATCH", json: { role: e.target.value } }); setMembers(await api(`/api/organizations/${org.id}/members`)); })}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select>
                      <button className="btn btn-danger" onClick={() => confirm(`Remove ${m.name}?`) && act(async () => { await api(`/api/organizations/${org.id}/members/${m.user_id}`, { method: "DELETE" }); setMembers(await api(`/api/organizations/${org.id}/members`)); })}>Remove</button></>) : <Badge>{m.role}</Badge>}
                    <span className="w-full text-[11px] text-mute md:w-auto">id {m.user_id}</span></li>))}</ul>
              </Card>
              {admin && (
                <Card title="Invite a member">
                  <div className="flex flex-wrap gap-2"><input className="input max-w-xs" type="email" aria-label="Email" placeholder="email" value={inv.email} onChange={(e) => setInv({ ...inv, email: e.target.value })} />
                    <select aria-label="Role" className="input w-auto" value={inv.role} onChange={(e) => setInv({ ...inv, role: e.target.value })}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select>
                    <button className="btn btn-primary" onClick={() => act(async () => { const r = await api(`/api/organizations/${org.id}/invitations`, { json: inv }); setToken(r.token); })}>Create invitation</button></div>
                  {token && <p className="mt-3 break-all rounded-lg bg-bg p-3 text-xs">Email delivery isn’t configured — send this one-time token to the invitee yourself (valid 7 days, tied to their email):<br /><code className="select-all">{token}</code></p>}
                </Card>
              )}
              {["owner", "admin", "manager"].includes(org.role) && (
                <Card title="Create a team workspace"><div className="flex gap-2"><input className="input max-w-xs" aria-label="Team name" placeholder="Team name" value={team} onChange={(e) => setTeam(e.target.value)} />
                  <button className="btn" onClick={() => act(async () => { await api(`/api/organizations/${org.id}/teams`, { json: { name: team } }); setTeam(""); await refresh(); await loadOrgs(); })}>Create</button></div>
                  <p className="mt-2 text-xs text-mute">Team workspaces have their own members; add members via the API (<code>member_ids</code>) for now.</p></Card>
              )}
              {admin && (
                <Card title="Audit log"><ul className="space-y-1 text-xs">{audit.map((a, i) => <li key={i} className="flex gap-3"><span className="w-36 shrink-0 text-mute">{fmtDateTime(a.at + "Z")}</span><span className="font-mono">{a.action}</span><span className="truncate text-mute">{a.resource_type} {a.resource_id?.slice(0, 8)}</span></li>)}{!audit.length && <li className="text-mute">No entries.</li>}</ul></Card>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}
