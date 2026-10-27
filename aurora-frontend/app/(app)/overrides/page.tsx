"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";

import { Badge, Card, EmptyState, ErrorState, LoadingBlock, PageHeader, Select, Stat, Table, Td, Th } from "@/components/ui";
import { fmtDate, fmtInt, fmtPctPoints } from "@/lib/format";
import { describeOverride, FVA_LABEL, FVA_TONE } from "@/lib/purchasing";
import type { OverrideValueAdded } from "@/lib/types";

export default function OverridesPage() {
  const [windowDays, setWindowDays] = useState(180);
  const fva = useSWR<OverrideValueAdded>(`/forecast-overrides/value-added?window_days=${windowDays}`);
  const d = fva.data;
  const overall = d?.overall;

  return (
    <>
      <PageHeader
        title="Forecast overrides"
        description="Planner adjustments to the forecast, and whether they beat the model. Each override is scored on the days it covered that now have actual sales: forecast value added is the model's error minus the override's error."
      />
      <Card
        title="Did overrides help?"
        className="mb-6"
        actions={
          <>
            <label className="text-xs text-muted" htmlFor="fw">Window</label>
            <Select id="fw" value={windowDays} onChange={(e) => setWindowDays(Number(e.target.value))} className="w-32">
              {[30, 90, 180, 365].map((w) => <option key={w} value={w}>{w} days</option>)}
            </Select>
          </>
        }
      >
        {fva.error ? (
          <ErrorState error={fva.error} onRetry={() => fva.mutate()} />
        ) : !d ? (
          <LoadingBlock rows={2} />
        ) : !overall || overall.scored_days === 0 ? (
          <p className="text-sm text-muted">No override has covered days with actual sales yet, so there is nothing to score.</p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Forecast value added" value={overall.fva_points == null ? "—" : `${overall.fva_points > 0 ? "+" : ""}${overall.fva_points.toFixed(1)} pts`} tone={FVA_TONE[overall.verdict] === "ok" ? "ok" : FVA_TONE[overall.verdict] === "bad" ? "bad" : "default"} hint="WAPE points; positive means overrides helped" />
            <Stat label="Model WAPE" value={fmtPctPoints(overall.model_wape)} />
            <Stat label="With overrides" value={fmtPctPoints(overall.override_wape)} />
            <Stat label="Days scored" value={fmtInt(overall.scored_days)} />
          </div>
        )}
      </Card>

      <Card title="Overrides" subtitle="Archived overrides are included so their record stays visible. Add or archive them on each product page.">
        {fva.error ? null : !d ? (
          <LoadingBlock rows={4} />
        ) : d.overrides.length === 0 ? (
          <EmptyState title="No overrides in this window" />
        ) : (
          <Table caption="Forecast overrides and their accuracy">
            <thead>
              <tr>
                <Th>SKU</Th>
                <Th>Override</Th>
                <Th>Dates</Th>
                <Th>Reason</Th>
                <Th className="text-right">Days scored</Th>
                <Th className="text-right">Model WAPE</Th>
                <Th className="text-right">Override WAPE</Th>
                <Th>Verdict</Th>
              </tr>
            </thead>
            <tbody>
              {d.overrides.map((o) => (
                <tr key={o.id}>
                  <Td><Link className="font-medium text-primary hover:underline" href={`/products/${o.product_id}`}>{o.sku ?? "—"}</Link></Td>
                  <Td>{describeOverride(o.kind, o.value)} {o.archived_at && <Badge>archived</Badge>}</Td>
                  <Td>{fmtDate(o.start_date)} – {fmtDate(o.end_date)}</Td>
                  <Td className="max-w-xs text-muted">{o.reason}</Td>
                  <Td className="text-right tabular-nums">{fmtInt(o.scored_days)}</Td>
                  <Td className="text-right tabular-nums">{fmtPctPoints(o.model_wape)}</Td>
                  <Td className="text-right tabular-nums">{fmtPctPoints(o.override_wape)}</Td>
                  <Td><Badge tone={FVA_TONE[o.verdict]}>{FVA_LABEL[o.verdict]}</Badge></Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
