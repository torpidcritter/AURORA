"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";

import { Badge, Button, Card, ConfirmButton, EmptyState, ErrorState, LoadingBlock, PageHeader, Pagination, RiskBadge, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Alert, Page } from "@/lib/types";

const LIMIT = 25;

export default function AlertsPage() {
  const { can } = useSession();
  const [status, setStatus] = useState("active");
  const [severity, setSeverity] = useState("");
  const [offset, setOffset] = useState(0);
  const [actionErr, setActionErr] = useState<unknown>(null);
  const qs = new URLSearchParams({ limit: String(LIMIT), offset: String(offset) });
  if (status) qs.set("status", status);
  if (severity) qs.set("severity", severity);
  const list = useSWR<Page<Alert>>(`/alerts?${qs.toString()}`, { keepPreviousData: true });

  const act = async (id: string, action: "acknowledge" | "resolve") => {
    setActionErr(null);
    try {
      await api(`/alerts/${id}/${action}`, { method: "POST" });
      await list.mutate();
    } catch (e) {
      setActionErr(e);
    }
  };

  return (
    <>
      <PageHeader title="Alerts" description="Stockout risk, drift, stale data and model accuracy alerts. Alerts resolve automatically when their condition clears." />
      <Card
        actions={
          <div className="flex gap-2">
            <label className="sr-only" htmlFor="as">Status</label>
            <Select id="as" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }} className="w-40">
              <option value="active">Open and acknowledged</option>
              <option value="open">Open</option>
              <option value="acknowledged">Acknowledged</option>
              <option value="resolved">Resolved</option>
              <option value="">All</option>
            </Select>
            <label className="sr-only" htmlFor="asv">Severity</label>
            <Select id="asv" value={severity} onChange={(e) => { setSeverity(e.target.value); setOffset(0); }} className="w-36">
              <option value="">All severities</option>
              {["critical", "high", "medium", "low"].map((s) => <option key={s} value={s}>{s}</option>)}
            </Select>
          </div>
        }
        title="Inbox"
      >
        {actionErr != null && <div className="mb-3"><ErrorState error={actionErr} /></div>}
        {list.error ? (
          <ErrorState error={list.error} onRetry={() => list.mutate()} />
        ) : !list.data ? (
          <LoadingBlock rows={6} />
        ) : list.data.items.length === 0 ? (
          <EmptyState title="No alerts">Nothing matches these filters.</EmptyState>
        ) : (
          <>
            <ul className="divide-y divide-line">
              {list.data.items.map((a) => (
                <li key={a.id} className="flex flex-wrap items-start gap-3 py-3">
                  <RiskBadge level={a.severity} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-ink">{a.title}</span>
                      <Badge>{a.kind.replace(/_/g, " ")}</Badge>
                      {a.status !== "open" && <Badge tone={a.status === "resolved" ? "ok" : "info"}>{a.status}</Badge>}
                    </div>
                    <p className="mt-1 text-sm text-muted">{a.message}</p>
                    <p className="mt-1 text-xs text-faint">
                      Raised {fmtDateTime(a.created_at)} · updated {fmtDateTime(a.updated_at)}
                      {a.product_id && (
                        <> · <Link className="text-primary hover:underline" href={`/products/${a.product_id}`}>open product</Link></>
                      )}
                    </p>
                  </div>
                  <div className="flex gap-1">
                    {can("acknowledge_alerts") && a.status === "open" && <Button variant="ghost" onClick={() => act(a.id, "acknowledge")}>Acknowledge</Button>}
                    {can("acknowledge_alerts") && a.status !== "resolved" && <Button variant="ghost" onClick={() => act(a.id, "resolve")}>Resolve</Button>}
                    {can("delete_alerts") && (
                      <ConfirmButton
                        title="Delete alert?"
                        body="The alert is removed permanently. If the condition still holds, monitoring will raise a new alert. The deletion is recorded in the audit log."
                        confirmLabel="Delete"
                        onConfirm={async () => {
                          await api(`/alerts/${a.id}`, { method: "DELETE" });
                          await list.mutate();
                        }}
                      >
                        Delete
                      </ConfirmButton>
                    )}
                  </div>
                </li>
              ))}
            </ul>
            <Pagination total={list.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
          </>
        )}
      </Card>
    </>
  );
}
