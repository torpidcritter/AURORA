"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";

import { Badge, Button, Card, EmptyState, ErrorState, LoadingBlock, Notice, PageHeader, Pagination, Select, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDate, fmtInt, fmtMoney } from "@/lib/format";
import { PO_STATUS_LABEL, PO_STATUS_TONE } from "@/lib/purchasing";
import { useSession } from "@/lib/session";
import type { Page, POStatus, POSummary, PurchaseOrder, SuggestResult } from "@/lib/types";

const FILTERS: { value: string; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "", label: "All" },
  ...(Object.keys(PO_STATUS_LABEL) as POStatus[]).map((s) => ({ value: s, label: PO_STATUS_LABEL[s] })),
];
const LIMIT = 50;

export default function PurchaseOrdersPage() {
  const router = useRouter();
  const { can } = useSession();
  const manage = can("manage_purchasing");
  const [status, setStatus] = useState("open");
  const [offset, setOffset] = useState(0);
  const list = useSWR<Page<POSummary>>(`/purchase-orders?limit=${LIMIT}&offset=${offset}${status ? `&status=${status}` : ""}`);
  const [busy, setBusy] = useState<"suggest" | "new" | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [result, setResult] = useState<SuggestResult | null>(null);

  const suggest = async () => {
    setBusy("suggest");
    setErr(null);
    setResult(null);
    try {
      const r = await api<SuggestResult>("/purchase-orders/suggest", { method: "POST", body: {} });
      setResult(r);
      setStatus("open");
      setOffset(0);
      await list.mutate();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  };

  const createEmpty = async () => {
    setBusy("new");
    setErr(null);
    try {
      const po = await api<PurchaseOrder>("/purchase-orders", { method: "POST", body: { lines: [] } });
      router.push(`/purchase-orders/${po.id}`);
    } catch (e) {
      setErr(e);
      setBusy(null);
    }
  };

  return (
    <>
      <PageHeader
        title="Purchase orders"
        description="Draft, approve, send and receive orders. Sending adds the quantities to stock on order; receiving moves them into stock on hand."
        actions={
          manage && (
            <>
              <Button variant="primary" loading={busy === "suggest"} disabled={busy != null} onClick={() => void suggest()}>
                Draft from reorder suggestions
              </Button>
              <Button loading={busy === "new"} disabled={busy != null} onClick={() => void createEmpty()}>
                New order
              </Button>
            </>
          )
        }
      />
      {err != null && <div className="mb-4"><ErrorState error={err} /></div>}
      {result && (
        <div className="mb-4">
          <Notice tone={result.created.length ? "ok" : "info"}>
            {result.created.length
              ? `Drafted ${result.created.length} order(s): ${result.created.map((o) => `${o.number} (${o.supplier_name ?? "no supplier yet"})`).join(", ")}.`
              : "No new drafts: nothing needs reordering right now."}{" "}
            Skipped {result.skipped.already_on_order} already on an unsent order, {result.skipped.no_reorder_needed} with enough stock and {result.skipped.no_data} without stock or sales data.
            Drafts are not approved or sent until someone does so.
          </Notice>
        </div>
      )}
      <Card
        title="Orders"
        actions={
          <>
            <label className="sr-only" htmlFor="pos">Status</label>
            <Select
              id="pos"
              value={status}
              onChange={(e) => {
                setStatus(e.target.value);
                setOffset(0);
              }}
              className="w-44"
            >
              {FILTERS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
            </Select>
          </>
        }
      >
        {list.error ? (
          <ErrorState error={list.error} onRetry={() => list.mutate()} />
        ) : !list.data ? (
          <LoadingBlock rows={6} />
        ) : list.data.items.length === 0 ? (
          <EmptyState title="No orders here">{manage ? "Draft orders from the reorder suggestions, or start one by hand." : null}</EmptyState>
        ) : (
          <>
            <Table caption="Purchase orders">
              <thead>
                <tr>
                  <Th>Order</Th>
                  <Th>Status</Th>
                  <Th>Supplier</Th>
                  <Th className="text-right">Lines</Th>
                  <Th className="text-right">Units</Th>
                  <Th className="text-right">Value</Th>
                  <Th>Expected</Th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((o) => (
                  <tr key={o.id} className="hover:bg-white/5">
                    <Td>
                      <Link href={`/purchase-orders/${o.id}`} className="font-medium text-primary hover:underline">{o.number}</Link>
                      {o.source === "suggested" && <div className="text-xs text-faint">from suggestions</div>}
                    </Td>
                    <Td><Badge tone={PO_STATUS_TONE[o.status]}>{PO_STATUS_LABEL[o.status]}</Badge></Td>
                    <Td>{o.supplier_name ?? <span className="text-warn">No supplier</span>}</Td>
                    <Td className="text-right tabular-nums">{fmtInt(o.line_count)}</Td>
                    <Td className="text-right tabular-nums">{fmtInt(o.total_units)}</Td>
                    <Td className="text-right tabular-nums">{fmtMoney(o.total_value)}{o.total_complete ? "" : "*"}</Td>
                    <Td>{fmtDate(o.expected_date)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            {list.data.items.some((o) => !o.total_complete) && <p className="mt-2 text-xs text-faint">*Some lines have no unit cost, so the value is incomplete.</p>}
            <Pagination total={list.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
          </>
        )}
      </Card>
    </>
  );
}
