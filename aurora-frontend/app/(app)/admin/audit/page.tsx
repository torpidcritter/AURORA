"use client";

import { useState } from "react";
import useSWR from "swr";

import { Badge, Button, Card, EmptyState, ErrorState, Input, LoadingBlock, Notice, PageHeader, Pagination, Select, Table, Td, Th } from "@/components/ui";
import { apiDownload, saveBlob } from "@/lib/api";
import { auditParams, dateRangeError } from "@/lib/audit";
import { fmtDateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { AuditEvent, Page } from "@/lib/types";

const LIMIT = 50;

export default function AuditPage() {
  const { can } = useSession();
  const [action, setAction] = useState("");
  const [actor, setActor] = useState("");
  const [outcome, setOutcome] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [offset, setOffset] = useState(0);
  const [exporting, setExporting] = useState<string | null>(null);
  const [exportErr, setExportErr] = useState<unknown>(null);
  const rangeErr = dateRangeError(from, to);
  const filters = auditParams({ action, actor, outcome, from, to });
  const qs = new URLSearchParams(filters);
  qs.set("limit", String(LIMIT));
  qs.set("offset", String(offset));
  const list = useSWR<Page<AuditEvent>>(can("view_audit") && !rangeErr ? `/audit?${qs.toString()}` : null, { keepPreviousData: true });

  const exportAs = async (format: "csv" | "jsonl") => {
    setExportErr(null);
    setExporting(format);
    try {
      const params = new URLSearchParams(filters);
      params.set("format", format);
      const { blob, filename } = await apiDownload(`/audit/export?${params.toString()}`);
      saveBlob(blob, filename);
    } catch (e) {
      setExportErr(e);
    } finally {
      setExporting(null);
    }
  };

  if (!can("view_audit")) return <Notice tone="warn">Your role cannot view the audit log.</Notice>;

  return (
    <>
      <PageHeader
        title="Audit log"
        description="Security-relevant and data-changing actions in your organisation, newest first. Exports use the same filters, list events oldest first, hold up to 50,000 events and are themselves recorded here. Retention is set under Settings."
      />
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
          <label className="flex items-center gap-2 text-xs text-muted">
            From (UTC)
            <Input type="date" aria-label="From date" value={from} onChange={(e) => { setFrom(e.target.value); setOffset(0); }} className="w-40" />
          </label>
          <label className="flex items-center gap-2 text-xs text-muted">
            To
            <Input type="date" aria-label="To date" value={to} onChange={(e) => { setTo(e.target.value); setOffset(0); }} className="w-40" />
          </label>
          <div className="ml-auto flex gap-2">
            <Button variant="secondary" disabled={!!rangeErr} loading={exporting === "csv"} onClick={() => void exportAs("csv")}>Export CSV</Button>
            <Button variant="secondary" disabled={!!rangeErr} loading={exporting === "jsonl"} onClick={() => void exportAs("jsonl")}>Export JSON Lines</Button>
          </div>
        </div>
        {rangeErr && <div className="mb-3"><Notice tone="warn">{rangeErr}</Notice></div>}
        {exportErr != null && <div className="mb-3"><ErrorState error={exportErr} /></div>}
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
