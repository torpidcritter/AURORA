"use client";

import Link from "next/link";
import useSWR from "swr";

import { TrendChart } from "@/components/charts";
import { GettingStarted } from "@/components/GettingStarted";
import { Badge, Card, EmptyState, ErrorState, LoadingBlock, PageHeader, RiskBadge, Stat } from "@/components/ui";
import { fmtDate, fmtInt, fmtMoney, fmtPctPoints } from "@/lib/format";
import type { ActionItem, Alert, Kpis, Page } from "@/lib/types";

export default function DashboardPage() {
  const kpi = useSWR<Kpis>("/intelligence/kpi");
  const trend = useSWR<{ as_of: string | null; points: { date: string; units: number }[] }>("/intelligence/demand-trend?days=90");
  const recs = useSWR<{ items: ActionItem[] }>("/intelligence/recommendations?limit=8");
  const alerts = useSWR<Page<Alert>>("/alerts?status=open&limit=5");
  const k = kpi.data;

  return (
    <>
      <PageHeader
        title="Dashboard"
        description={k?.as_of ? `Figures are relative to the latest sales date in your data: ${fmtDate(k.as_of)}.` : "Executive overview of demand, inventory risk and alerts."}
      />
      <GettingStarted />
      {kpi.error ? (
        <ErrorState error={kpi.error} onRetry={() => kpi.mutate()} />
      ) : !k ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="h-24 animate-pulse rounded-xl bg-slate-700/20" />
          ))}
        </div>
      ) : k.products_total === 0 ? (
        <EmptyState title="No data yet" action={<Link className="text-primary hover:underline" href="/upload">Upload sales history</Link>}>
          Upload historical sales to see demand, forecasts and inventory risk.
        </EmptyState>
      ) : (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Stat
            label="Inventory health"
            value={k.inventory_health_score == null ? "—" : fmtInt(k.inventory_health_score)}
            hint={k.inventory_health_score == null ? "Add on-hand stock to score" : "0–100, higher is better"}
            tone={k.inventory_health_score == null ? "default" : k.inventory_health_score >= 70 ? "ok" : k.inventory_health_score >= 45 ? "warn" : "bad"}
            title={k.definitions.inventory_health_score}
          />
          <Stat label="Critical / high risk" value={`${k.risk_counts.critical} / ${k.risk_counts.high}`} hint={`${k.products_with_inventory} of ${k.products_total} products scored`} tone={k.risk_counts.critical ? "bad" : k.risk_counts.high ? "warn" : "ok"} />
          <Stat label="Reorder now" value={fmtInt(k.reorder_now)} hint={k.reorder_value ? `${fmtMoney(k.reorder_value)} at unit cost` : "products at or below reorder point"} title={k.definitions.reorder_now} />
          <Stat label="Open alerts" value={fmtInt(k.open_alerts)} hint={`${k.critical_alerts} critical`} tone={k.critical_alerts ? "bad" : "default"} />
          <Stat label="Units, last 30 days" value={fmtInt(k.units_last_30d)} hint={k.demand_change_pct == null ? "no prior period" : `${k.demand_change_pct >= 0 ? "+" : ""}${fmtPctPoints(k.demand_change_pct)} vs prior 30 days`} title={k.definitions.units_last_30d} />
          <Stat label="Forecast, next 7 days" value={fmtInt(k.forecast_units_next_7d)} hint="products with known stock" title={k.definitions.forecast_units_next_7d} />
          <Stat label="Overstock" value={fmtInt(k.overstock_units)} hint={k.overstock_value ? fmtMoney(k.overstock_value) : "units above order-up-to"} title={k.definitions.overstock_units} />
          <Stat label="Active model" value={k.active_model?.version ?? "None"} hint={k.active_model ? `WAPE ${fmtPctPoints(k.active_model.wape)}` : "statistical methods in use"} tone="ai" />
        </div>
      )}

      <div className="mt-6 grid gap-6 xl:grid-cols-3">
        <Card title="Units sold per day" subtitle="All products, last 90 days" className="xl:col-span-2">
          {trend.error ? (
            <ErrorState error={trend.error} onRetry={() => trend.mutate()} />
          ) : !trend.data ? (
            <LoadingBlock rows={6} />
          ) : trend.data.points.length ? (
            <TrendChart points={trend.data.points} label="Units sold per day across all products" />
          ) : (
            <EmptyState title="No sales history" />
          )}
        </Card>
        <Card title="Open alerts" actions={<Link href="/alerts" className="text-xs text-primary hover:underline">View all</Link>}>
          {alerts.error ? (
            <ErrorState error={alerts.error} onRetry={() => alerts.mutate()} />
          ) : !alerts.data ? (
            <LoadingBlock />
          ) : alerts.data.items.length ? (
            <ul className="space-y-3">
              {alerts.data.items.map((a) => (
                <li key={a.id} className="flex items-start gap-2">
                  <RiskBadge level={a.severity} />
                  <span className="text-sm text-ink">{a.title}</span>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="No open alerts" />
          )}
        </Card>
      </div>

      <Card title="Recommended actions" subtitle="Highest urgency first. Each line is computed from the forecast and your stock settings." className="mt-6">
        {recs.error ? (
          <ErrorState error={recs.error} onRetry={() => recs.mutate()} />
        ) : !recs.data ? (
          <LoadingBlock rows={4} />
        ) : recs.data.items.length ? (
          <ul className="divide-y divide-line">
            {recs.data.items.map((r) => (
              <li key={`${r.type}-${r.product_id}`} className="flex flex-col gap-1 py-3 sm:flex-row sm:items-start sm:gap-4">
                <div className="flex shrink-0 items-center gap-2 sm:w-40">
                  {r.risk_level ? <RiskBadge level={r.risk_level} /> : <Badge tone={r.type === "overstock" ? "warn" : "default"}>{r.type}</Badge>}
                </div>
                <div className="min-w-0 flex-1">
                  <Link href={`/products/${r.product_id}`} className="text-sm font-medium text-ink hover:text-primary">
                    {r.title}
                  </Link>
                  <p className="mt-0.5 text-sm text-muted">{r.detail}</p>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title="Nothing needs action">All products with known stock are above their reorder points.</EmptyState>
        )}
      </Card>
    </>
  );
}
