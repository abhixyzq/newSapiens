"use client";
import { useEffect, useRef, useState } from "react";
import { Mic, Pause, Play, Square, Upload } from "lucide-react";
import { api } from "@/lib/api";
import { useApp } from "./shell";
import { ErrorNote } from "./ui";

type Mode = "record" | "upload" | "transcript";

/** Explicit, visible, consent-gated capture. Uses the browser's own microphone permission prompt; nothing records
 *  until the user ticks consent and presses Start. Chunks upload every 10 s with retry; uploads can be resumed. */
export function NewSession({ onDone }: { onDone: (id: string) => void }) {
  const { ws } = useApp();
  const [mode, setMode] = useState<Mode>("record");
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<"meeting" | "conversation" | "voice_note">("conversation");
  const [shared, setShared] = useState(false);
  const [sensitive, setSensitive] = useState(false);
  const [others, setOthers] = useState(false);
  const [consent, setConsent] = useState(false);
  const [notified, setNotified] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [when, setWhen] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [state, setState] = useState<"idle" | "recording" | "paused" | "uploading">("idle");
  const [secs, setSecs] = useState(0);
  const rec = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const sid = useRef<string | null>(null);
  const idx = useRef(0);
  const pending = useRef<Promise<any>[]>([]);
  const failed = useRef<{ i: number; b: Blob }[]>([]);

  useEffect(() => {
    if (state !== "recording") return;
    const t = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [state]);
  useEffect(() => () => stream.current?.getTracks().forEach((t) => t.stop()), []);

  const needNotice = kind === "meeting" || others;
  const create = async (source: string, consentBody: any) => {
    const body: any = { title: title || "Untitled session", kind, source, visibility: shared ? "shared" : "private", exclude_from_processing: sensitive, consent: consentBody };
    if (when) body.occurred_at = new Date(when).toISOString();
    const s = await api("/api/sessions", { json: body });
    return s.id as string;
  };

  const sendChunk = async (i: number, blob: Blob, tries = 4): Promise<void> => {
    for (let a = 0; a < tries; a++) {
      try {
        const f = new FormData(); f.append("file", blob, `chunk-${i}.webm`); f.append("chunk_index", String(i));
        await api(`/api/sessions/${sid.current}/upload`, { form: f });
        return;
      } catch (e) { await new Promise((r) => setTimeout(r, 800 * 2 ** a)); }
    }
    failed.current.push({ i, b: blob });
  };

  const start = async () => {
    setErr(null);
    if (!consent || (needNotice && !notified)) return setErr("Please confirm recording consent first.");
    try {
      const ms = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.current = ms;
      sid.current = await create("mic_desktop", { self_recording: true, has_other_participants: others, participants_notified: notified, statement: "Confirmed in-app before recording" });
      await api(`/api/sessions/${sid.current}/start`, { method: "POST" });
      const mt = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
      const r = new MediaRecorder(ms, mt ? { mimeType: mt } : undefined);
      r.ondataavailable = (e) => { if (e.data.size) { const i = idx.current++; pending.current.push(sendChunk(i, e.data)); } };
      r.start(10000);
      rec.current = r; setSecs(0); setState("recording");
    } catch (e: any) { setErr(e.name === "NotAllowedError" ? "Microphone permission was denied in your browser." : e.message); stream.current?.getTracks().forEach((t) => t.stop()); }
  };
  const pause = async () => { rec.current?.pause(); await api(`/api/sessions/${sid.current}/pause`, { method: "POST" }); setState("paused"); };
  const resume = async () => { rec.current?.resume(); await api(`/api/sessions/${sid.current}/resume`, { method: "POST" }); setState("recording"); };
  const stop = async () => {
    setState("uploading");
    const r = rec.current!;
    await new Promise<void>((res) => { r.onstop = () => res(); r.stop(); });
    stream.current?.getTracks().forEach((t) => t.stop());
    await Promise.all(pending.current);
    for (const f of [...failed.current]) { failed.current = failed.current.filter((x) => x !== f); await sendChunk(f.i, f.b, 6); }
    if (failed.current.length) { setErr("Some audio chunks could not be uploaded. Check your connection and press Stop again to retry."); setState("paused"); return; }
    try { await api(`/api/sessions/${sid.current}/stop`, { method: "POST" }); onDone(sid.current!); } catch (e: any) { setErr(e.message); }
  };

  const submitFile = async () => {
    setBusy(true); setErr(null);
    try {
      if (!consent) throw new Error("Please confirm you have the right to process this recording.");
      const id = await create(mode === "upload" ? "upload" : "transcript_import", { upload_rights: true, statement: "Uploader attests rights" });
      if (mode === "upload") {
        if (!file) throw new Error("Choose an audio file");
        const f = new FormData(); f.append("file", file); f.append("chunk_index", "0"); f.append("finalize", "true");
        await api(`/api/sessions/${id}/upload`, { form: f });
      } else {
        if (!text.trim()) throw new Error("Paste a transcript");
        await api(`/api/sessions/${id}/transcript`, { json: { text } });
      }
      onDone(id);
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };

  const mm = String(Math.floor(secs / 60)).padStart(2, "0"), ss = String(secs % 60).padStart(2, "0");
  const live = state !== "idle";
  return (
    <div className="card p-5">
      {live && (
        <div role="status" className="mb-4 flex items-center justify-between rounded-lg border border-bad/40 bg-bad/10 px-4 py-3">
          <span className="flex items-center gap-2 text-sm font-medium text-bad"><span className={`h-2.5 w-2.5 rounded-full bg-bad ${state === "recording" ? "rec-dot" : ""}`} />
            {state === "recording" ? "Recording" : state === "paused" ? "Paused" : "Uploading…"} · <span className="tabular-nums">{mm}:{ss}</span></span>
          <span className="flex gap-2">
            {state === "recording" && <button className="btn" onClick={pause}><Pause size={14} /> Pause</button>}
            {state === "paused" && <button className="btn" onClick={resume}><Play size={14} /> Resume</button>}
            {state !== "uploading" && <button className="btn btn-primary" onClick={stop}><Square size={14} /> Stop & process</button>}
          </span>
        </div>
      )}
      {!live && (
        <>
          <div role="tablist" className="mb-4 flex gap-1 rounded-lg bg-bg p-1 text-sm">
            {([["record", "Record", Mic], ["upload", "Upload audio", Upload], ["transcript", "Paste transcript", Upload]] as const).map(([m, l, I]) => (
              <button key={m} role="tab" aria-selected={mode === m} onClick={() => setMode(m)} className={`flex-1 rounded-md px-3 py-1.5 ${mode === m ? "bg-panel font-medium shadow-sm" : "text-mute"}`}><I size={14} className="mr-1 inline" />{l}</button>
            ))}
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <div><label className="label" htmlFor="t">Title</label><input id="t" className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Website redesign review" /></div>
            <div><label className="label" htmlFor="k">Type</label>
              <select id="k" className="input" value={kind} onChange={(e) => setKind(e.target.value as any)}><option value="conversation">Conversation</option><option value="meeting">Meeting</option><option value="voice_note">Voice note</option></select></div>
            {mode !== "record" && <div><label className="label" htmlFor="w">When did it happen? (optional)</label><input id="w" type="datetime-local" className="input" value={when} onChange={(e) => setWhen(e.target.value)} /></div>}
            {mode === "upload" && <div><label className="label" htmlFor="f">Audio file</label><input id="f" type="file" accept="audio/*,video/mp4,video/webm,.m4a,.opus" className="input" onChange={(e) => setFile(e.target.files?.[0] || null)} /></div>}
          </div>
          {mode === "transcript" && <div className="mt-3"><label className="label" htmlFor="tx">Transcript (<code>[mm:ss] Speaker: text</code> per line)</label><textarea id="tx" rows={7} className="input font-mono text-xs" value={text} onChange={(e) => setText(e.target.value)} /></div>}
          <div className="mt-4 space-y-2 text-sm">
            {mode === "record" && <label className="flex items-start gap-2"><input type="checkbox" className="mt-1" checked={others} onChange={(e) => setOthers(e.target.checked)} /> Other people can be heard in this recording</label>}
            {mode === "record" && needNotice && <label className="flex items-start gap-2"><input type="checkbox" className="mt-1" checked={notified} onChange={(e) => setNotified(e.target.checked)} /> I have told all participants that this conversation is being recorded and they agree.</label>}
            <label className="flex items-start gap-2"><input type="checkbox" className="mt-1" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
              {mode === "record" ? "I am starting this recording myself and I am entitled to record it. Recording is visible on screen and stops when I press Stop." : "I have the right to process this recording/transcript."}</label>
            {ws.type !== "individual" && <label className="flex items-start gap-2"><input type="checkbox" className="mt-1" checked={shared} onChange={(e) => setShared(e.target.checked)} /> Share with everyone in “{ws.name}” (otherwise only you can see it)</label>}
            <label className="flex items-start gap-2"><input type="checkbox" className="mt-1" checked={sensitive} onChange={(e) => setSensitive(e.target.checked)} /> Sensitive — store only; do not transcribe, analyse, search or report on it</label>
          </div>
          <div className="mt-4"><ErrorNote error={err} /></div>
          <div className="mt-4">{mode === "record" ? <button className="btn btn-primary" onClick={start}><Mic size={14} /> Start recording</button>
            : <button className="btn btn-primary" onClick={submitFile} disabled={busy}>{busy ? "Uploading…" : mode === "upload" ? "Upload & process" : "Import & process"}</button>}</div>
        </>
      )}
      {live && <div className="mt-3"><ErrorNote error={err} /></div>}
    </div>
  );
}
