"use client";
import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { NewSession } from "@/components/capture";
import { useApp } from "@/components/shell";
import { PageHeader } from "@/components/ui";
import { SessionList } from "@/components/sessionlist";

function Page() {
  const sp = useSearchParams();
  const router = useRouter();
  const { canWrite } = useApp();
  const [show, setShow] = useState(sp.get("new") === "1");
  return (
    <>
      <PageHeader title="Conversations" sub="Every captured, uploaded or imported session." actions={canWrite && <button className="btn btn-primary" onClick={() => setShow(!show)}>{show ? "Close" : "New session"}</button>} />
      {show && canWrite && <div className="mb-6"><NewSession onDone={(id) => router.push(`/conversations/${id}`)} /></div>}
      <SessionList />
    </>
  );
}
export default function C() { return <Suspense><Page /></Suspense>; }
