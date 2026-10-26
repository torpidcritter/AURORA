"use client";

import Link from "next/link";
import useSWR from "swr";

import { Card, EmptyState, ErrorState, LoadingBlock, Notice, Stat, Table, Td, Th } from "@/components/ui";
import { fmtDate, fmtInt, fmtPctPoints, methodLabel } from "@/lib/format";
import type { LiveAccuracy } from "@/lib/types";

const WORST = 5;

/** What the live-accuracy alert sees right now, in words. Exported for tests. */
export function liveAccuracyVerdict(a: LiveAccuracy): { tone: "info" | "ok" | "warn"; text: string } {
  const name = a.alert_metric.toUpperCase();
  const value = a[a.alert_metric];
  if (a.points === 0) {
    return {
      tone: "info",
      text: `No served forecast has been scored yet. Live accuracy appears once forecasts have been generated and the sales for the days they covered have been uploaded. Until then, rely on the held-out validation results below.`,
    };
  }
  if (!a.sufficient) {
    return {
      tone: "info",
      text: `Not enough data yet: ${fmtInt(a.points)} of the ${fmtInt(a.min_points)} product-days needed have been scored, so these figures are not reliable and no alert is raised. Rely on the held-out validation results below for now.`,
    };
  }
  if (value == null) {
    return { tone: "info", text: `Every scored day had zero demand, so live ${name} is undefined and no alert is raised.` };
  }
  if (value > a.alert_threshold) {
    return {
      tone: "warn",
      text: `Live ${name} is ${fmtPctPoints(value)}, above the ${a.alert_threshold}% alert threshold. Monitoring raises an alert; review recent data and consider retraining (a new version is promoted only if it passes the quality gate).`,
    };
  }
  return { tone: "ok", text: `Live ${name} is ${fmtPctPoints(value)}, within the ${a.alert_threshold}% alert threshold.` };
}

export function LiveAccuracyCard({ className }: { className?: string }) {
  const acc = useSWR<LiveAccuracy>("/intelligence/accuracy");
  const a = acc.data;
  const windowRange = a?.window_start && a.window_end ? `${fmtDate(a.window_start)} – ${fmtDate(a.window_end)}` : "No scored days yet";
  const metricTone = (m: "wape" | "mape") => {
    if (!a || !a.sufficient || a.alert_metric !== m || a[m] == null) return "default" as const;
    return a[m]! > a.alert_threshold ? ("warn" as const) : ("ok" as const);
  };
  const methods = a ? Object.entries(a.by_method).sort((x, y) => (x[1].wape ?? Infinity) - (y[1].wape ?? Infinity)) : [];
  const worst = a ? a.by_product.filter((p) => p.points >= a.min_points && p.wape != null).slice(0, WORST) : [];
  const verdict = a ? liveAccuracyVerdict(a) : null;

  return (
    <Card
      title="Live accuracy"
      subtitle="Forecasts that were actually served, scored against the sales that arrived afterwards. Each product-day uses the freshest forecast made before that day."
      className={className}
    >
      {acc.error ? (
        <ErrorState error={acc.error} onRetry={() => acc.mutate()} />
      ) : !a || !verdict ? (
        <LoadingBlock rows={3} label="Loading live accuracy" />
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Live WAPE" value={fmtPctPoints(a.wape)} tone={metricTone("wape")} hint="Error relative to total demand, every scored day" />
            <Stat
              label="Live MAPE"
              value={fmtPctPoints(a.mape)}
              tone={metricTone("mape")}
              hint={a.mape_excluded_zero_days ? `${fmtInt(a.mape_excluded_zero_days)} zero-demand days skipped` : "Average error per day with demand"}
            />
            <Stat label="Scored product-days" value={fmtInt(a.points)} hint={`At least ${fmtInt(a.min_points)} needed to judge`} />
            <Stat label="Window" value={`${a.window_days} days`} hint={windowRange} />
          </div>
          <Notice tone={verdict.tone}>
            <span className="font-medium">
              Alert rule: live {a.alert_metric.toUpperCase()} above {a.alert_threshold}%.
            </span>{" "}
            {verdict.text}
          </Notice>
          {methods.length === 0 && worst.length === 0 ? (
            a.points > 0 ? null : <EmptyState title="Nothing scored yet">Generate forecasts, then upload the sales for the days they cover.</EmptyState>
          ) : (
            <div className="grid gap-6 xl:grid-cols-2">
              {methods.length > 0 && (
                <div>
                  <h3 className="mb-2 text-sm font-medium text-ink">By forecasting method</h3>
                  <Table caption="Live accuracy by forecasting method">
                    <thead>
                      <tr>
                        <Th>Method</Th>
                        <Th className="text-right">Scored days</Th>
                        <Th className="text-right">WAPE</Th>
                        <Th className="text-right">MAPE</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {methods.map(([m, v]) => (
                        <tr key={m}>
                          <Td>{methodLabel(m)}</Td>
                          <Td className="text-right tabular-nums">{fmtInt(v.points)}</Td>
                          <Td className="text-right tabular-nums">{fmtPctPoints(v.wape)}</Td>
                          <Td className="text-right tabular-nums">{fmtPctPoints(v.mape)}</Td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                </div>
              )}
              {worst.length > 0 && (
                <div>
                  <h3 className="mb-2 text-sm font-medium text-ink">Least accurate products</h3>
                  <Table caption="Least accurate products by live WAPE">
                    <thead>
                      <tr>
                        <Th>SKU</Th>
                        <Th className="text-right">Scored days</Th>
                        <Th className="text-right">WAPE</Th>
                        <Th className="text-right">MAPE</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {worst.map((p) => (
                        <tr key={p.product_id}>
                          <Td>
                            <Link href={`/products/${p.product_id}`} className="text-primary hover:underline">
                              {p.sku ?? p.product_id.slice(0, 8)}
                            </Link>
                          </Td>
                          <Td className="text-right tabular-nums">{fmtInt(p.points)}</Td>
                          <Td className="text-right tabular-nums">{fmtPctPoints(p.wape)}</Td>
                          <Td className="text-right tabular-nums">{fmtPctPoints(p.mape)}</Td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                  <p className="mt-2 text-xs text-faint">
                    Products with at least {a.min_points} scored days. See every product&apos;s health in{" "}
                    <Link href="/products" className="text-primary hover:underline">
                      Products
                    </Link>
                    .
                  </p>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
