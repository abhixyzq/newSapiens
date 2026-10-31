"use client";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Copy, FileDown, MessageSquarePlus, RefreshCw, Send, Trash2 } from "lucide-react";
import { api, download, getWs } from "@/lib/api";
import { Answer, Citation, SourceList, SourcePanel } from "@/components/answer";
import { Empty, ErrorNote } from "@/components/ui";

type Msg = { id?: string; role: "user" | "assistant"; content: string; citations: Citation[]; meta?: any; streaming?: boolean };

function Ask() {
  const params = useSearchParams();
  const [chats, setChats] = useState<any[]>([]);
  const [chatId, setChatId] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [sugg, setSugg] = useState<string[]>([]);
  const [src, setSrc] = useState<Citation | null>(null);
  const [filter, setFilter] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const started = useRef(false);

  const loadChats = useCallback((q = "") => api<any[]>("/api/chat/history" + (q ? `?q=${encodeURIComponent(q)}` : "")).then(setChats).catch(() => {}), []);
  useEffect(() => { loadChats(); api<string[]>("/api/chat/suggestions").then(setSugg).catch(() => {}); }, [loadChats]);
  useEffect(() => { const t = setTimeout(() => loadChats(filter), 250); return () => clearTimeout(t); }, [filter, loadChats]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);

  const open = async (id: string) => {
    const c = await api(`/api/chat/${id}`);
    setChatId(id); setMsgs(c.messages); setErr(null);
  };

  const send = useCallback(async (text: string, regenerate = false) => {
    if (!text.trim() && !regenerate) return;
    setBusy(true); setErr(null);
    setMsgs((m) => regenerate ? m.filter((x, i) => !(i === m.length - 1 && x.role === "assistant")) : [...m, { role: "user", content: text, citations: [] }]);
    setInput("");
    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      const ws = getWs(); if (ws) headers["X-Workspace-Id"] = ws;
      const res = await fetch("/api/chat/stream", { method: "POST", headers, credentials: "include", body: JSON.stringify({ message: text, chat_id: chatId, regenerate }) });
      if (!res.ok || !res.body) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
      setMsgs((m) => [...m, { role: "assistant", content: "", citations: [], streaming: true }]);
      const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
      for (;;) {
        const { done, value } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const ev = buf.slice(0, i); buf = buf.slice(i + 2);
          const type = /event: (\w+)/.exec(ev)?.[1]; const data = JSON.parse(/data: (.*)/s.exec(ev)?.[1] || "null");
          if (type === "meta") setChatId(data.chat_id);
          else if (type === "token") setMsgs((m) => { const c = [...m]; c[c.length - 1] = { ...c[c.length - 1], content: c[c.length - 1].content + data }; return c; });
          else if (type === "done") setMsgs((m) => { const c = [...m]; c[c.length - 1] = { ...data, streaming: false }; return c; });
        }
      }
      loadChats();
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }, [chatId, loadChats]);

  useEffect(() => { const q = params.get("q"); if (q && !started.current) { started.current = true; send(q); } }, [params, send]);

  const last = [...msgs].reverse().find((m) => m.role === "assistant");
  return (
    <div className="-mx-4 -my-6 flex h-[calc(100vh-3.5rem)] md:-mx-8 md:-my-8">
      <aside className="hidden w-64 shrink-0 flex-col border-r border-line bg-panel p-3 lg:flex">
        <button className="btn btn-primary mb-2" onClick={() => { setChatId(null); setMsgs([]); }}><MessageSquarePlus size={16} /> New chat</button>
        <input className="input mb-2" placeholder="Search chats" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Search chats" />
        <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto">
          {chats.map((c) => (
            <div key={c.id} className={`group flex items-center rounded-lg px-2 py-1.5 text-sm ${c.id === chatId ? "bg-bg" : "hover:bg-bg"}`}>
              <button className="min-w-0 flex-1 truncate text-left" onClick={() => open(c.id)}>{c.title}</button>
              <button className="invisible text-mute hover:text-bad group-hover:visible" aria-label="Delete chat" onClick={async () => { await api(`/api/chat/${c.id}`, { method: "DELETE" }); if (c.id === chatId) { setChatId(null); setMsgs([]); } loadChats(); }}><Trash2 size={14} /></button>
            </div>
          ))}
          {!chats.length && <p className="px-2 text-xs text-mute">No chats yet.</p>}
        </div>
      </aside>
      <section className="flex min-w-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6 md:px-8">
          <div className="mx-auto max-w-3xl space-y-6">
            {!msgs.length && (
              <div className="pt-10">
                <h1 className="text-2xl font-semibold tracking-tight">Ask Sapiens</h1>
                <p className="mt-1 max-w-xl text-sm text-mute">Ask anything. Questions about your work are answered from your recordings, transcripts, tasks and decisions, with a source you can open for every claim; anything else gets a general answer.</p>
                <div className="mt-6 grid gap-2 sm:grid-cols-2">{sugg.map((s) => <button key={s} className="card p-3 text-left text-sm hover:border-accent" onClick={() => send(s)}>{s}</button>)}</div>
              </div>
            )}
            {msgs.map((m, i) => (
              <div key={i} className={m.role === "user" ? "flex justify-end" : ""}>
                {m.role === "user" ? <div className="max-w-[85%] rounded-2xl bg-navy px-4 py-2.5 text-sm text-white dark:bg-accent/20 dark:text-ink">{m.content}</div> : (
                  <div className="card p-5">
                    {m.streaming && !m.content ? <span className="text-sm text-mute">Searching your records…</span> : <Answer content={m.content} citations={m.citations} onCite={setSrc} />}
                    {!m.streaming && <SourceList citations={m.citations} onCite={setSrc} />}
                    {!m.streaming && m.id && (
                      <div className="mt-3 flex flex-wrap gap-2 border-t border-line pt-3">
                        <button className="btn" onClick={() => navigator.clipboard.writeText(m.content)}><Copy size={14} /> Copy</button>
                        {m === last && <button className="btn" disabled={busy} onClick={() => send("", true)}><RefreshCw size={14} /> Regenerate</button>}
                        <button className="btn" onClick={() => download(`/api/chat/${chatId}/messages/${m.id}/pdf`, "sapiens-answer.pdf")}><FileDown size={14} /> Export PDF</button>
                        {m.meta?.mode && m.meta.mode !== "deterministic" && <span className="self-center text-xs text-mute">{m.meta.mode}</span>}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
            <ErrorNote error={err} />
            <div ref={end} />
          </div>
        </div>
        <form className="border-t border-line bg-panel p-4" onSubmit={(e) => { e.preventDefault(); send(input); }}>
          <div className="mx-auto flex max-w-3xl gap-2">
            <input className="input" placeholder={msgs.length ? "Ask a follow-up…" : "e.g. What happened in the meeting on 25 January?"} value={input} onChange={(e) => setInput(e.target.value)} disabled={busy} aria-label="Message" />
            <button className="btn btn-primary" disabled={busy || !input.trim()} aria-label="Send"><Send size={16} /></button>
          </div>
        </form>
      </section>
      {src && <SourcePanel c={src} onClose={() => setSrc(null)} />}
    </div>
  );
}

export default function Page() { return <Suspense><Ask /></Suspense>; }
