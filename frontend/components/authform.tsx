"use client";
import Link from "next/link";
import { useState } from "react";
import { Activity } from "lucide-react";
import { api, setWsStored } from "@/lib/api";
import { ErrorNote } from "./ui";

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const [f, setF] = useState({ name: "", email: "", password: "" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reg = mode === "register";
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
      const r = await api(`/api/auth/${mode}`, { json: reg ? { ...f, timezone: tz } : { email: f.email, password: f.password } });
      const w = r.user.workspaces.find((x: any) => x.type === "individual") || r.user.workspaces[0];
      setWsStored(w.id);
      location.href = "/overview";
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="grid min-h-screen place-items-center px-4">
      <form onSubmit={submit} className="card w-full max-w-sm space-y-4 p-7">
        <div className="flex items-center gap-2 text-navy dark:text-white"><Activity className="text-accent" size={22} /><span className="font-semibold tracking-[0.18em]">NEW SAPIENS</span></div>
        <p className="text-sm text-mute">Your Workday. Understood. Documented. Optimized.</p>
        {reg && <div><label className="label" htmlFor="n">Name</label><input id="n" className="input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} autoComplete="name" /></div>}
        <div><label className="label" htmlFor="e">Email</label><input id="e" type="email" className="input" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} autoComplete="email" /></div>
        <div><label className="label" htmlFor="p">Password {reg && <span className="text-mute">(min. 10 characters)</span>}</label>
          <input id="p" type="password" className="input" required minLength={reg ? 10 : 1} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} autoComplete={reg ? "new-password" : "current-password"} /></div>
        <ErrorNote error={err} />
        <button className="btn btn-primary w-full" disabled={busy}>{busy ? "Please wait…" : reg ? "Create account" : "Sign in"}</button>
        <p className="text-center text-sm text-mute">{reg ? <>Have an account? <Link className="text-accent" href="/login">Sign in</Link></> : <>New here? <Link className="text-accent" href="/register">Create an account</Link></>}</p>
      </form>
    </div>
  );
}
