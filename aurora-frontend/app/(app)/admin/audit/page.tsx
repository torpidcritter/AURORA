"use client";

import { useState } from "react";
import useSWR from "swr";

import { Badge, Card, EmptyState, ErrorState, Input, LoadingBlock, Notice, PageHeader, Pagination, Select, Table, Td, Th } from "@/components/ui";
import { fmtDateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { AuditEvent, Page } from "@/lib/types";

const LIMIT = 50;

export default function AuditPage() {
  const { can } = useSession();
  const [action, setAction] = useState("");
  const [actor, setActor] = useState("");
  const [outcome, setOutcome] = useState("");
  const [offset, setOffset] = useState(0);
  const qs = new URLSearchParams({ limit: String(LIMIT), offset: String(offset) });
  if (action.trim()) qs.set("action", action.trim());
  if (actor.trim()) qs.set("actor", actor.trim());
  if (outcome) qs.set("outcome", outcome);
  const list = useSWR<Page<AuditEvent>>(can("view_audit") ? `/audit?${qs.toString()}` : null, { keepPreviousData: true });

  if (!can("view_audit")) return <Notice tone="warn">Your role cannot view the audit log.</Notice>;

  return (
    <>
      <PageHeader title="Audit log" description="Security-relevant and data-changing actions in your organisation, newest first." />
      <Card>
        <div className="mb-4 flex flex-wrap gap-3">
          <label className="sr-only" htmlFor="aa">Action prefix</label>
          <Input id="aa" placeholder="Action prefix, e.g. auth. or model." value={action} onChange={(e) => { setAction(e.target.value); setOffset(0); }} className="max-w-xs" />
          <label className="sr-only" htmlFor="au">Actor email</label>
          <Input id="au" placeholder="Actor email" value={actor} onChange={(e) => { setActor(e.target.value); setOffset(0); }} className="max-w-xs" />
          <label className="sr-only" htmlFor="ao">Outcome</label>
          <Select id="ao" value={outcome} onChange={(e) => { setOutcome(e.target.value); setOffset(0); }} className="w-40">
            <option value="">All outcomes</option>
            <option value="success">Success</option>
            <option value="failure">Failure</option>
          </Select>
        </div>
        {list.error ? (
          <ErrorState error={list.error} onRetry={() => list.mutate()} />
        ) : !list.data ? (
          <LoadingBlock rows={8} />
        ) : list.data.items.length === 0 ? (
          <EmptyState title="No matching events" />
        ) : (
          <>
            <Table caption="Audit events">
              <thead>
                <tr>
                  <Th>Time</Th>
                  <Th>Action</Th>
                  <Th>Actor</Th>
                  <Th>Resource</Th>
                  <Th>Outcome</Th>
                  <Th>Details</Th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((e) => (
                  <tr key={e.id}>
                    <Td className="whitespace-nowrap">{fmtDateTime(e.created_at)}</Td>
                    <Td><span className="font-mono text-xs">{e.action}</span></Td>
                    <Td>{e.actor_email ?? "system"}{e.ip ? <div className="text-xs text-faint">{e.ip}</div> : null}</Td>
                    <Td className="text-xs">{e.resource_type ? `${e.resource_type}${e.resource_id ? ` ${e.resource_id.slice(0, 8)}` : ""}` : "—"}</Td>
                    <Td><Badge tone={e.outcome === "success" ? "ok" : "bad"}>{e.outcome}</Badge></Td>
                    <Td>
                      {Object.keys(e.details ?? {}).length ? (
                        <details>
                          <summary className="cursor-pointer text-xs text-primary">View</summary>
                          <pre className="mt-1 max-w-md overflow-x-auto whitespace-pre-wrap text-xs text-muted">{JSON.stringify(e.details, null, 2)}</pre>
                          {e.request_id && <div className="text-xs text-faint">request {e.request_id}</div>}
                        </details>
                      ) : (
                        "—"
                      )}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <Pagination total={list.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
          </>
        )}
      </Card>
    </>
  );
}
