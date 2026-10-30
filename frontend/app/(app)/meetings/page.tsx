"use client";
import { PageHeader } from "@/components/ui";
import { SessionList } from "@/components/sessionlist";

export default function Meetings() {
  return (<><PageHeader title="Meetings" sub="Sessions you marked as meetings. Change a session’s type on its detail page." /><SessionList kind="meeting" /></>);
}
