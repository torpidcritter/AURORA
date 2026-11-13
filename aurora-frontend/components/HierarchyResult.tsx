"use client";

import Link from "next/link";

import { DemandForecastChart } from "@/components/charts";
import { Badge, Card, Notice, Stat, Table, Td, Th } from "@/components/ui";
import { fmt1, fmtInt, fmtPct, fmtPctPoints, methodLabel } from "@/lib/format";
import { RECONCILE_LABEL, summarise } from "@/lib/hierarchy";
import type { HierarchyForecast } from "@/lib/types";

/** A category or organisation forecast: the group line, how it was chosen, and the products that add up to it. */
export function HierarchyResult({ f }: { f: HierarchyForecast }) {
  const { total } = summarise(f);
  const { direct, bottom_up } = f.backtest.wape;
  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label={`${f.label}: next ${f.horizon} days`} value={`${fmtInt(total)} units`} hint={`Data as of ${f.as_of}`} />
        <Stat label="Products forecast" value={`${f.products_forecast} of ${f.products}`} hint={f.excluded.length ? `${f.excluded.length} without a forecast of their own` : "all products"} />
        <Stat label="Sum of products (backtest error)" value={fmtPctPoints(bottom_up)} hint="WAPE on the group's history" tone={f.recommended === "bottom_up" ? "ok" : "default"} />
        <Stat label={`Group model, ${methodLabel(f.direct_method)}`} value={fmtPctPoints(direct)} hint="WAPE on the group's history" tone={f.recommended === "top_down" ? "ok" : "default"} />
      </div>
      <Notice tone={f.reconcile === f.recommended ? "ok" : "warn"}>
        Using <strong>{RECONCILE_LABEL[f.reconcile]}</strong>.{" "}
        {f.reconcile === f.recommended
          ? `It had the lower error over ${f.backtest.folds} backtest periods.`
          : `${RECONCILE_LABEL[f.recommended]} had the lower error over ${f.backtest.folds} backtest periods.`}{" "}
        Product numbers below always add up to the group total. {f.interval.note}
      </Notice>
      {f.warnings.map((w) => <Notice key={w} tone="warn">{w}</Notice>)}
      <Card title={`${f.label} demand`}>
        <DemandForecastChart
          history={f.history.map((h) => ({ ...h, imputed: false }))}
          forecast={f.points.map((p) => ({ date: p.date, yhat: p.yhat, lower: p.lower, upper: p.upper }))}
          coverage={f.interval.calibrated ? f.coverage : undefined}
        />
      </Card>
      <Card title="Products in this forecast" subtitle="Units over the horizon. With the group model, each product keeps its share of the product forecasts.">
        <Table caption="Product breakdown">
          <thead>
            <tr>
              <Th>SKU</Th>
              <Th>Name</Th>
              <Th className="text-right">Units</Th>
              <Th className="text-right">Share</Th>
              <Th className="text-right">Own forecast</Th>
              <Th>Method</Th>
            </tr>
          </thead>
          <tbody>
            {f.breakdown.map((b) => (
              <tr key={b.product_id}>
                <Td><Link className="font-medium text-primary hover:underline" href={`/products/${b.product_id}`}>{b.sku}</Link></Td>
                <Td>{b.name}</Td>
                <Td className="text-right tabular-nums">{fmt1(b.units)}</Td>
                <Td className="text-right tabular-nums">{fmtPct(b.share, 1)}</Td>
                <Td className="text-right tabular-nums">{fmt1(b.own_forecast_units)}</Td>
                <Td>
                  {b.method ? methodLabel(b.method) : "—"} {b.overrides > 0 && <Badge tone="ai">override</Badge>}
                </Td>
              </tr>
            ))}
            <tr>
              <Td className="font-medium">Total</Td>
              <Td />
              <Td className="text-right font-medium tabular-nums">{fmt1(total)}</Td>
              <Td className="text-right tabular-nums">100%</Td>
              <Td />
              <Td />
            </tr>
          </tbody>
        </Table>
        {f.excluded.length > 0 && (
          <p className="mt-3 text-xs text-muted">
            Not forecast on their own: {f.excluded.map((e) => `${e.sku} (${e.reason})`).join(", ")}.
          </p>
        )}
      </Card>
    </div>
  );
}
