"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Badge, Card, PageHeader } from "@/components/ui";

export default function Integrations() {
  const [rows, setRows] = useState<any[]>([]);
  const [health, setHealth] = useState<any>(null);
  useEffect(() => { api("/api/integrations").then(setRows); fetch("/api/health").then((r) => r.json()).then(setHealth); }, []);
  return (
    <>
      <PageHeader title="Integrations" sub="Only integrations that actually work are marked Available. Meeting-platform connectors are not built yet." />
      <div className="grid gap-4 md:grid-cols-2">
        {rows.map((r) => (
          <Card key={r.id}><div className="flex items-center justify-between"><h3 className="font-medium">{r.name}</h3><Badge tone={r.status === "available" ? "ok" : "mute"}>{r.status === "available" ? "Available" : "Not built"}</Badge></div><p className="mt-2 text-sm text-mute">{r.note}</p></Card>
        ))}
      </div>
      {health && (
        <Card title="Processing providers (server configuration)" className="mt-6">
          <dl className="grid grid-cols-2 gap-2 text-sm md:grid-cols-4">
            {[["Speech-to-text", health.stt_provider], ["LLM", health.llm_provider], ["Embeddings", health.embedding_provider], ["Database", health.database]].map(([k, v]) => <div key={k}><dt className="text-xs text-mute">{k}</dt><dd className="font-medium">{v}</dd></div>)}
          </dl>
          <p className="mt-3 text-xs text-mute">Set via environment variables (see <code>.env.example</code>). With STT = none, uploaded audio waits for a transcript; with LLM = rule_based, extraction uses conservative heuristics and flags items for review.</p>
        </Card>
      )}
    </>
  );
}
