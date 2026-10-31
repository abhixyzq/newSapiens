"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { Activity, BarChart3, Bell, BookOpen, Briefcase, CheckSquare, ChevronsLeft, ChevronsRight, FileText, FolderKanban, LayoutDashboard,
  MessageSquareText, Mic, Moon, Plug, Search, Settings, Sun, Users } from "lucide-react";
import { api, getWs, setWsStored } from "@/lib/api";
import { Spinner } from "./ui";

type Workspace = { id: string; name: string; type: string; role: string; organization_id: string | null; report_time: string; timezone: string; auto_daily_report: boolean };
type Me = { id: string; name: string; email: string; timezone: string; audio_retention_days: number | null; stt_languages: string; workspaces: Workspace[]; organizations: { id: string; name: string; role: string }[] };
type Ctx = { me: Me; ws: Workspace; switchWs: (id: string) => void; refresh: () => Promise<void>; canWrite: boolean };
const C = createContext<Ctx>(null as any);
export const useApp = () => useContext(C);

const NAV = [
  ["/overview", "Overview", LayoutDashboard], ["/ask", "Ask Sapiens", MessageSquareText], ["/reports", "Daily Reports", FileText],
  ["/meetings", "Meetings", Users], ["/conversations", "Conversations", Mic], ["/tasks", "Tasks & Commitments", CheckSquare],
  ["/projects", "Projects", FolderKanban], ["/memory", "Knowledge Memory", BookOpen], ["/analytics", "Analytics", BarChart3],
  ["/team", "Team Workspace", Briefcase], ["/integrations", "Integrations", Plug], ["/settings", "Settings", Settings],
] as const;

export function AppShell({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [wsId, setWsId] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [dark, setDark] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const path = usePathname();

  const refresh = useCallback(async () => {
    const m = await api<Me>("/api/auth/me");
    setMe(m);
    const saved = getWs();
    const pick = m.workspaces.find((w) => w.id === saved) || m.workspaces.find((w) => w.type === "individual") || m.workspaces[0];
    setWsStored(pick.id);
    setWsId(pick.id);
  }, []);

  useEffect(() => {
    refresh().catch(() => {});
    setCollapsed(localStorage.getItem("ns_collapsed") === "1");
    const t = localStorage.getItem("ns_theme");
    const d = t ? t === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    setDark(d);
    document.documentElement.classList.toggle("dark", d);
  }, [refresh]);
  useEffect(() => { setMobileNav(false); }, [path]);

  if (!me || !wsId) return <div className="grid h-screen place-items-center"><Spinner /></div>;
  const ws = me.workspaces.find((w) => w.id === wsId)!;
  const switchWs = (id: string) => { setWsStored(id); setWsId(id); location.href = "/overview"; };
  const toggleTheme = () => { const d = !dark; setDark(d); localStorage.setItem("ns_theme", d ? "dark" : "light"); document.documentElement.classList.toggle("dark", d); };
  const toggleCollapse = () => { localStorage.setItem("ns_collapsed", collapsed ? "0" : "1"); setCollapsed(!collapsed); };

  const nav = (
    <nav aria-label="Primary" className="flex-1 space-y-0.5 overflow-y-auto px-2 py-3">
      {NAV.map(([href, label, Icon]) => {
        const on = path === href || path.startsWith(href + "/");
        return (
          <Link key={href} href={href} title={collapsed ? label : undefined} aria-current={on ? "page" : undefined}
            className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${on ? "bg-white/10 text-white" : "text-slate-300 hover:bg-white/5 hover:text-white"}`}>
            <Icon size={18} className="shrink-0" />
            {!collapsed && <span className="truncate">{label}</span>}
          </Link>
        );
      })}
    </nav>
  );

  return (
    <C.Provider value={{ me, ws, switchWs, refresh, canWrite: ws.role !== "viewer" }}>
      <div className="flex h-screen overflow-hidden">
        <aside className={`hidden shrink-0 flex-col bg-navy transition-[width] duration-200 md:flex ${collapsed ? "w-[68px]" : "w-60"}`}>
          <div className="flex h-14 items-center gap-2 px-4 text-white">
            <Activity size={20} className="shrink-0 text-accent" />
            {!collapsed && <span className="text-sm font-semibold tracking-[0.18em]">NEW SAPIENS</span>}
          </div>
          {nav}
          <button onClick={toggleCollapse} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} className="m-2 flex items-center justify-center rounded-lg p-2 text-slate-300 hover:bg-white/5">
            {collapsed ? <ChevronsRight size={16} /> : <ChevronsLeft size={16} />}
          </button>
        </aside>
        {mobileNav && (
          <div className="fixed inset-0 z-40 md:hidden" onClick={() => setMobileNav(false)}>
            <div className="h-full w-64 bg-navy" onClick={(e) => e.stopPropagation()}>
              <div className="flex h-14 items-center gap-2 px-4 text-white"><Activity size={20} className="text-accent" /><span className="text-sm font-semibold tracking-[0.18em]">NEW SAPIENS</span></div>
              {nav}
            </div>
          </div>
        )}
        <div className="flex min-w-0 flex-1 flex-col">
          <Topbar onMenu={() => setMobileNav(true)} dark={dark} toggleTheme={toggleTheme} />
          <main className="min-h-0 flex-1 overflow-y-auto"><div className="mx-auto max-w-6xl px-4 py-6 md:px-8 md:py-8">{children}</div></main>
        </div>
      </div>
    </C.Provider>
  );
}

function Topbar({ onMenu, dark, toggleTheme }: { onMenu: () => void; dark: boolean; toggleTheme: () => void }) {
  const { me, ws, switchWs } = useApp();
  const router = useRouter();
  const [q, setQ] = useState("");
  const [res, setRes] = useState<any>(null);
  const [openN, setOpenN] = useState(false);
  const [notes, setNotes] = useState<any[]>([]);
  const [openU, setOpenU] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<any[]>("/api/notifications").then(setNotes).catch(() => {});
    const h = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); document.getElementById("global-search")?.focus(); } };
    const c = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) { setRes(null); setOpenN(false); setOpenU(false); } };
    window.addEventListener("keydown", h); window.addEventListener("click", c);
    return () => { window.removeEventListener("keydown", h); window.removeEventListener("click", c); };
  }, []);
  useEffect(() => {
    if (q.trim().length < 2) { setRes(null); return; }
    const t = setTimeout(() => api("/api/search?q=" + encodeURIComponent(q)).then(setRes).catch(() => setRes(null)), 250);
    return () => clearTimeout(t);
  }, [q]);

  const unread = notes.filter((n) => !n.read).length;
  const go = (href: string) => { setRes(null); setQ(""); router.push(href); };
  return (
    <header ref={box} className="relative z-30 flex h-14 shrink-0 items-center gap-3 border-b border-line bg-panel px-4">
      <button className="btn md:hidden" onClick={onMenu} aria-label="Open menu">☰</button>
      <div className="relative w-full max-w-md">
        <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-mute" />
        <input id="global-search" className="input pl-9" placeholder="Search conversations, tasks, decisions…  (Ctrl K)" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Global search" />
        {res && (
          <div className="card absolute left-0 right-0 top-11 max-h-[70vh] overflow-y-auto p-2 shadow-xl">
            {!res.sessions.length && !res.tasks.length && !res.decisions.length && !res.passages.length && <p className="p-3 text-sm text-mute">No matches in what you have access to.</p>}
            {res.sessions.map((s: any) => <Row key={s.id} k="Recording" t={s.title} onClick={() => go(`/conversations/${s.id}`)} />)}
            {res.decisions.map((d: any) => <Row key={d.id} k="Decision" t={d.text} onClick={() => go(`/conversations/${d.session_id}`)} />)}
            {res.tasks.map((t: any) => <Row key={t.id} k="Task" t={t.title} onClick={() => go("/tasks")} />)}
            {res.passages.map((p: any, i: number) => <Row key={i} k={`Transcript · ${p.title}`} t={p.text} onClick={() => go(`/conversations/${p.session_id}`)} />)}
            <button className="btn mt-2 w-full" onClick={() => go("/ask?q=" + encodeURIComponent(q))}>Ask Sapiens: “{q}”</button>
          </div>
        )}
      </div>
      <div className="ml-auto flex items-center gap-2">
        {me.workspaces.length > 1 && (
          <select aria-label="Workspace" className="input w-auto max-w-[180px] py-1.5" value={ws.id} onChange={(e) => switchWs(e.target.value)}>
            {me.workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}{w.type !== "individual" ? ` (${w.type})` : ""}</option>)}
          </select>
        )}
        <button className="btn" onClick={toggleTheme} aria-label="Toggle theme">{dark ? <Sun size={16} /> : <Moon size={16} />}</button>
        <div className="relative">
          <button className="btn relative" aria-label="Notifications" onClick={() => setOpenN(!openN)}>
            <Bell size={16} />{unread > 0 && <span className="absolute -right-1 -top-1 rounded-full bg-accent px-1.5 text-[10px] font-semibold text-[#04121c]">{unread}</span>}
          </button>
          {openN && (
            <div className="card absolute right-0 top-11 w-80 p-2 shadow-xl">
              {!notes.length && <p className="p-3 text-sm text-mute">Nothing new.</p>}
              {notes.slice(0, 8).map((n) => <Row key={n.id} k={n.read ? "" : "New"} t={n.title} onClick={() => n.link && go(n.link)} />)}
              {unread > 0 && <button className="btn mt-1 w-full" onClick={() => api("/api/notifications/read", { method: "POST" }).then(() => setNotes(notes.map((n) => ({ ...n, read: true }))))}>Mark all read</button>}
            </div>
          )}
        </div>
        <div className="relative">
          <button className="grid h-9 w-9 place-items-center rounded-full bg-navy text-sm font-semibold text-white" aria-label="Account" onClick={() => setOpenU(!openU)}>{me.name[0]?.toUpperCase()}</button>
          {openU && (
            <div className="card absolute right-0 top-11 w-56 p-2 shadow-xl">
              <div className="px-3 py-2"><div className="text-sm font-medium">{me.name}</div><div className="truncate text-xs text-mute">{me.email}</div></div>
              <button className="btn mt-1 w-full" onClick={() => go("/settings")}>Settings</button>
              <button className="btn mt-1 w-full" onClick={async () => { await api("/api/auth/logout", { method: "POST" }); localStorage.removeItem("ns_ws"); location.href = "/login"; }}>Sign out</button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}

function Row({ k, t, onClick }: { k: string; t: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className="block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-bg">
      {k && <span className="mr-2 text-[11px] font-medium uppercase tracking-wide text-accent">{k}</span>}
      <span className="line-clamp-2">{t}</span>
    </button>
  );
}
