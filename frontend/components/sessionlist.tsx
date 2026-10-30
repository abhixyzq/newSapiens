"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fmtDateTime, fmtDur } from "@/lib/format";
import { Empty, Spinner, StatusBadge } from "./ui";

export function SessionList({ kind }: { kind?: string }) {
  const [data, setData] = useState<{ total: number; items: any[] } | null>(null);
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const load = useCallback(() => api(`/api/sessions?limit=20&offset=${page * 20}${kind ? `&kind=${kind}` : ""}${q ? `&q=${encodeURIComponent(q)}` : ""}`).then(setData), [kind, q, page]);
  useEffect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [load]);
  return (
    <>
      <input className="input mb-4 max-w-sm" placeholder="Filter by title" value={q} onChange={(e) => { setPage(0); setQ(e.target.value); }} aria-label="Filter" />
      {!data ? <Spinner /> : !data.items.length ? <Empty title="Nothing here yet" hint="Captured and imported sessions appear here with their processing status." /> : (
        <div className="card divide-y divide-line">
          {data.items.map((s) => (
            <Link key={s.id} href={`/conversations/${s.id}`} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 hover:bg-bg">
              <div className="min-w-0 flex-1"><div className="truncate text-sm font-medium">{s.title}</div><div className="text-xs text-mute">{fmtDateTime(s.occurred_at)} · {s.kind.replace("_", " ")} · {s.visibility}</div></div>
              <span className="text-xs text-mute">{fmtDur(s.duration_seconds)}</span><StatusBadge status={s.status} />
            </Link>
          ))}
        </div>
      )}
      {data && data.total > 20 && (
        <div className="mt-3 flex items-center gap-2 text-sm"><button className="btn" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
          <span className="text-mute">{page * 20 + 1}–{Math.min((page + 1) * 20, data.total)} of {data.total}</span>
          <button className="btn" disabled={(page + 1) * 20 >= data.total} onClick={() => setPage(page + 1)}>Next</button></div>
      )}
    </>
  );
}

