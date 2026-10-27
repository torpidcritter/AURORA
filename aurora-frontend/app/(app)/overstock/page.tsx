"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";

import { Badge, Card, EmptyState, ErrorState, LoadingBlock, Notice, PageHeader, Pagination, Select, Stat, Table, Td, Th } from "@/components/ui";
import { fmt1, fmtDate, fmtInt, fmtMoney } from "@/lib/format";
import type { OverstockReport } from "@/lib/types";

const LIMIT = 100;

export default function OverstockPage() {
  const [cover, setCover] = useState(180);
  const [dead, setDead] = useState(90);
  const [offset, setOffset] = useState(0);
  const report = useSWR<OverstockReport>(`/inventory/overstock?cover_days=${cover}&dead_days=${dead}&limit=${LIMIT}&offset=${offset}`);
  const r = report.data;

  return (
    <>
      <PageHeader
        title="Overstock and dead stock"
        description="Where cash is tied up in stock that is not selling, based on the last 90 days of sales. Suggestions describe the situation; Aurora does not model how discounts change demand."
      />
      <Card
        title="Thresholds"
        className="mb-6"
        actions={
          <>
            <label className="text-xs text-muted" htmlFor="oc">Excess beyond</label>
            <Select
              id="oc"
              value={cover}
              onChange={(e) => {
                setCover(Number(e.target.value));
                setOffset(0);
              }}
              className="w-32"
            >
              {[60, 90, 120, 180, 270, 365].map((d) => <option key={d} value={d}>{d} days</option>)}
            </Select>
            <label className="text-xs text-muted" htmlFor="od">Dead after</label>
            <Select
              id="od"
              value={dead}
              onChange={(e) => {
                setDead(Number(e.target.value));
                setOffset(0);
              }}
              className="w-32"
            >
              {[30, 60, 90, 180, 365].map((d) => <option key={d} value={d}>{d} days</option>)}
            </Select>
          </>
        }
      >
        {!r ? (
          report.error ? null : <LoadingBlock rows={1} />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Excess stock value" value={fmtMoney(r.summary.excess_value)} tone={r.summary.excess_value > 0 ? "warn" : "default"} hint={`stock beyond ${r.cover_days} days of demand`} />
            <Stat label="Value of flagged stock" value={fmtMoney(r.summary.stock_value)} />
            <Stat label="Dead-stock products" value={fmtInt(r.summary.dead)} tone={r.summary.dead ? "bad" : "default"} hint={`no sales in ${r.dead_days} days`} />
            <Stat label="Overstocked products" value={fmtInt(r.summary.overstock)} />
          </div>
        )}
        {r && (r.summary.missing_cost ?? 0) > 0 && (
          <div className="mt-3"><Notice tone="warn">{r.summary.missing_cost} flagged product(s) have no unit cost, so their value is not counted.</Notice></div>
        )}
      </Card>

      <Card title="Flagged products" subtitle={r?.as_of ? `Relative to the latest sales date, ${fmtDate(r.as_of)}. Largest value tied up first.` : undefined}>
        {report.error ? (
          <ErrorState error={report.error} onRetry={() => report.mutate()} />
        ) : !r ? (
          <LoadingBlock rows={6} />
        ) : r.items.length === 0 ? (
          <EmptyState title={r.as_of ? "Nothing is overstocked at these thresholds" : "No sales data yet"} />
        ) : (
          <>
            <Table caption="Overstocked and dead-stock products">
              <thead>
                <tr>
                  <Th>SKU</Th>
                  <Th>Status</Th>
                  <Th className="text-right">On hand</Th>
                  <Th className="text-right">Sales/day</Th>
                  <Th className="text-right">Days of cover</Th>
                  <Th>Last sale</Th>
                  <Th className="text-right">Excess units</Th>
                  <Th className="text-right">Excess value</Th>
                  <Th>Suggestion</Th>
                </tr>
              </thead>
              <tbody>
                {r.items.map((i) => (
                  <tr key={i.product_id}>
                    <Td>
                      <Link className="font-medium text-primary hover:underline" href={`/products/${i.product_id}`}>{i.sku}</Link>
                      <div className="text-xs text-faint">{i.name}</div>
                    </Td>
                    <Td><Badge tone={i.status === "dead" ? "bad" : "warn"}>{i.status === "dead" ? "Dead stock" : "Overstock"}</Badge></Td>
                    <Td className="text-right tabular-nums">{fmtInt(i.on_hand)}{i.on_order ? <div className="text-xs text-faint">+{fmtInt(i.on_order)} on order</div> : null}</Td>
                    <Td className="text-right tabular-nums">{fmt1(i.avg_daily_demand)}</Td>
                    <Td className="text-right tabular-nums">{i.days_of_cover == null ? "no recent sales" : fmtInt(i.days_of_cover)}</Td>
                    <Td className="whitespace-nowrap">
                      {i.last_sale_date ? (
                        <>
                          {fmtDate(i.last_sale_date)}
                          <div className="text-xs text-faint">{i.days_since_last_sale ? `${i.days_since_last_sale} days ago` : "most recent day"}</div>
                        </>
                      ) : (
                        "never"
                      )}
                    </Td>
                    <Td className="text-right tabular-nums">{fmtInt(i.excess_units)}</Td>
                    <Td className="text-right tabular-nums">{fmtMoney(i.excess_value)}</Td>
                    <Td className="max-w-sm text-xs text-muted">{i.suggestions.join(" ")}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <Pagination total={r.total} limit={LIMIT} offset={offset} onChange={setOffset} />
          </>
        )}
      </Card>
    </>
  );
}
