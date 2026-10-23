"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";

import { Badge, Card, EmptyState, ErrorState, LoadingBlock, Notice, PageHeader, RiskBadge, Select, Table, Td, Th } from "@/components/ui";
import { fmt1, fmtInt, fmtMoney, fmtPct } from "@/lib/format";
import { RISK_ORDER, useRiskMatrix } from "@/lib/hooks";
import type { ActionItem } from "@/lib/types";

export default function InventoryPage() {
  const risk = useRiskMatrix();
  const actions = useSWR<{ items: ActionItem[] }>("/intelligence/recommendations?limit=20");
  const [level, setLevel] = useState("all");
  const [onlyReorder, setOnlyReorder] = useState(false);

  const rows = useMemo(() => {
    const items = risk.data?.items ?? [];
    return items
      .filter((r) => level === "all" || r.risk_level === level)
      .filter((r) => !onlyReorder || r.should_reorder)
      .sort((a, b) => (RISK_ORDER[a.risk_level] ?? 9) - (RISK_ORDER[b.risk_level] ?? 9) || (b.urgency ?? -1) - (a.urgency ?? -1));
  }, [risk.data, level, onlyReorder]);
  const unknown = (risk.data?.items ?? []).filter((r) => r.status === "unknown_inventory").length;

  return (
    <>
      <PageHeader title="Recommendations" description="Reorder quantities and stock positions for every product, most urgent first." />
      <Card title="Action list" subtitle="Highest-priority actions across reorders, overstock and data gaps" className="mb-6">
        {actions.error ? (
          <ErrorState error={actions.error} onRetry={() => actions.mutate()} />
        ) : !actions.data ? (
          <LoadingBlock />
        ) : actions.data.items.length === 0 ? (
          <EmptyState title="Nothing needs action" />
        ) : (
          <ol className="space-y-2">
            {actions.data.items.map((a, i) => (
              <li key={`${a.product_id}-${a.type}-${i}`} className="flex flex-wrap items-start gap-2 text-sm">
                {a.risk_level ? <RiskBadge level={a.risk_level} /> : <Badge tone={a.type === "data" ? "warn" : "info"}>{a.type}</Badge>}
                <Link href={`/products/${a.product_id}`} className="font-medium text-primary hover:underline">{a.title}</Link>
                <span className="text-muted">{a.detail}</span>
              </li>
            ))}
          </ol>
        )}
      </Card>

      <Card
        title="All products"
        actions={
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-xs text-muted">
              <input type="checkbox" checked={onlyReorder} onChange={(e) => setOnlyReorder(e.target.checked)} />
              Reorder needed only
            </label>
            <label className="sr-only" htmlFor="lvl">Risk level</label>
            <Select id="lvl" value={level} onChange={(e) => setLevel(e.target.value)} className="w-36">
              <option value="all">All risk levels</option>
              {["critical", "high", "medium", "low", "unknown"].map((l) => <option key={l} value={l}>{l}</option>)}
            </Select>
          </div>
        }
      >
        {unknown > 0 && (
          <div className="mb-3">
            <Notice tone="warn">
              {unknown} product(s) have no on-hand count, so they get no recommendation. Add stock on the product page or upload an inventory snapshot.
            </Notice>
          </div>
        )}
        {risk.error ? (
          <ErrorState error={risk.error} onRetry={() => risk.mutate()} />
        ) : !risk.data ? (
          <LoadingBlock rows={8} label="Computing recommendations" />
        ) : rows.length === 0 ? (
          <EmptyState title="No products match" />
        ) : (
          <Table caption="Inventory recommendations">
            <thead>
              <tr>
                <Th>SKU</Th>
                <Th>Risk</Th>
                <Th className="text-right">Urgency</Th>
                <Th className="text-right">On hand</Th>
                <Th className="text-right">Reorder point</Th>
                <Th className="text-right">Safety stock</Th>
                <Th className="text-right">Days of stock</Th>
                <Th className="text-right">P(stockout)</Th>
                <Th className="text-right">Order qty</Th>
                <Th className="text-right">Order value</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.product_id} className="hover:bg-white/5">
                  <Td>
                    <Link className="font-medium text-primary hover:underline" href={`/products/${r.product_id}`}>{r.sku}</Link>
                    <div className="text-xs text-faint">{r.name}</div>
                  </Td>
                  <Td><RiskBadge level={r.risk_level} /></Td>
                  {r.status === "ok" ? (
                    <>
                      <Td className="text-right tabular-nums">{fmtInt(r.urgency)}</Td>
                      <Td className="text-right tabular-nums">{fmtInt(r.on_hand)}</Td>
                      <Td className="text-right tabular-nums">{fmtInt(r.reorder_point)}</Td>
                      <Td className="text-right tabular-nums">{fmtInt(r.safety_stock)}</Td>
                      <Td className="text-right tabular-nums">{r.days_until_stockout == null ? "—" : fmt1(r.days_until_stockout)}</Td>
                      <Td className="text-right tabular-nums">{fmtPct(r.stockout_probability_lead_time)}</Td>
                      <Td className="text-right tabular-nums">{r.should_reorder ? <strong>{fmtInt(r.recommended_order_qty)}</strong> : "—"}</Td>
                      <Td className="text-right tabular-nums">{r.should_reorder ? fmtMoney(r.order_value) : "—"}</Td>
                    </>
                  ) : (
                    <Td colSpan={8} className="text-muted">{r.message ?? r.status.replace(/_/g, " ")}</Td>
                  )}
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
