"use client";

import { useState, type ReactNode } from "react";
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

import { fmt1, fmtPct, fmtShortDate } from "@/lib/format";
import type { AdjustedPoint, ForecastPoint, HistoryPoint, Recommendation } from "@/lib/types";

/**
 * Colour roles from the design system (spec 9.2). A colour always means the same thing on every chart:
 *   - observed demand (actuals): neutral slate, solid line
 *   - forecasts and primary indicators: sky #38bdf8
 *   - scenarios, ML training and AI: violet #a78bfa
 *   - status: emerald healthy/low, amber warning/medium, orange high, rose critical
 * Sky and violet are hard to tell apart with deuteranopia (OKLab ΔE ≈ 5), and amber/orange are close for everyone, so
 * series never rely on colour alone: forecasts and scenarios are dashed, risk levels have their own marker shape, and
 * every multi-series chart has a legend, labelled tooltips and a data-table view.
 */
export const ROLE = {
  actual: "#cbd5e1",
  forecast: "#38bdf8",
  ai: "#a78bfa",
  aiSoft: "#ddd6fe",
} as const;
export const STATUS = { critical: "#fb7185", high: "#fb923c", medium: "#fbbf24", low: "#34d399", unknown: "#94a3b8" };
/** Marker shape per risk level, so the scatter does not depend on hue. */
export const STATUS_SHAPE = { critical: "triangle", high: "diamond", medium: "square", low: "circle" } as const;
export const DASH = { forecast: "6 4", scenario: "6 4", validation: "3 3" } as const;
// Tick labels use the faint text token (#7c8aa1), which keeps 11px labels above 4.5:1 on the chart surfaces.
const AXIS = { stroke: "#7c8aa1", fontSize: 11, tickLine: false } as const;
const GRID = { stroke: "rgba(148,163,184,0.10)", vertical: false } as const;
const LEGEND_STYLE = { fontSize: 12, color: "#94a3b8" } as const;

function TooltipBox({ title, rows }: { title: string; rows: { label: string; value: string; color?: string }[] }) {
  return (
    <div className="glass-strong rounded-lg border border-line px-3 py-2 text-xs shadow-xl">
      <div className="mb-1 font-medium text-ink">{title}</div>
      {rows.map((r) => (
        <div key={r.label} className="flex items-center justify-between gap-4">
          <span className="flex items-center gap-1.5 text-muted">
            {r.color && <span className="h-2 w-2 rounded-full" style={{ background: r.color }} aria-hidden />}
            {r.label}
          </span>
          <span className="tabular text-ink">{r.value}</span>
        </div>
      ))}
    </div>
  );
}

/** Figure wrapper with an accessible data-table toggle. */
export function ChartFrame({ label, height = 280, table, children }: { label: string; height?: number; table: ReactNode; children: ReactNode }) {
  const [showTable, setShowTable] = useState(false);
  return (
    <figure aria-label={label}>
      {showTable ? (
        <div className="max-h-[320px] overflow-auto rounded-lg border border-line">{table}</div>
      ) : (
        <div style={{ height }} className="w-full">
          {children}
        </div>
      )}
      <figcaption className="mt-2 flex justify-end">
        <button type="button" className="text-xs text-muted underline-offset-2 hover:text-ink hover:underline" onClick={() => setShowTable((v) => !v)}>
          {showTable ? "Show chart" : "Show data table"}
        </button>
      </figcaption>
    </figure>
  );
}

function SimpleTable({ cols, rows }: { cols: string[]; rows: (string | number)[][] }) {
  return (
    <table className="w-full text-xs">
      <thead className="sticky top-0 bg-surface">
        <tr>
          {cols.map((c) => (
            <th key={c} scope="col" className="px-2 py-1.5 text-left font-medium text-muted">
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-t border-line/50">
            {r.map((v, j) => (
              <td key={j} className="tabular px-2 py-1">
                {v}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

interface DemandRow {
  date: string;
  actual?: number;
  imputed?: boolean;
  forecast?: number;
  band?: [number, number];
  /** Planner-adjusted demand, only on days an override covers. */
  adjusted?: number;
}

export function DemandForecastChart({
  history,
  forecast,
  coverage,
  adjusted,
}: {
  history: HistoryPoint[];
  forecast?: ForecastPoint[];
  coverage?: number;
  adjusted?: AdjustedPoint[];
}) {
  const rows: DemandRow[] = history.map((h) => ({ date: h.date, actual: h.units, imputed: h.imputed }));
  if (forecast?.length && rows.length) {
    // connect the forecast line to the last actual so the hand-off is visible
    const last = rows[rows.length - 1]!;
    last.forecast = last.actual;
  }
  const adj = new Map((adjusted ?? []).filter((a) => a.yhat_model != null).map((a) => [a.date, a.yhat]));
  for (const f of forecast ?? []) {
    rows.push({
      date: f.date,
      forecast: f.yhat,
      band: f.lower != null && f.upper != null ? [f.lower, f.upper] : undefined,
      adjusted: adj.get(f.date),
    });
  }
  const splitDate = forecast?.[0]?.date;
  const covLabel = coverage ? `${Math.round(coverage * 100)}% interval` : "Interval";
  return (
    <ChartFrame
      label="Daily demand history and forecast"
      table={
        <SimpleTable
          cols={["Date", "Actual", "Forecast", "Lower", "Upper", ...(adj.size ? ["With override"] : [])]}
          rows={rows.map((r) => [
            r.date,
            r.actual != null ? fmt1(r.actual) + (r.imputed ? " (imputed)" : "") : "",
            r.forecast != null && r.actual == null ? fmt1(r.forecast) : "",
            r.band ? fmt1(r.band[0]) : "",
            r.band ? fmt1(r.band[1]) : "",
            ...(adj.size ? [r.adjusted != null ? fmt1(r.adjusted) : ""] : []),
          ])}
        />
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey="date" tickFormatter={fmtShortDate} minTickGap={40} {...AXIS} />
          <YAxis {...AXIS} width={48} allowDecimals={false} />
          <Tooltip
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null;
              const r = payload[0]!.payload as DemandRow;
              const out: { label: string; value: string; color?: string }[] = [];
              if (r.actual != null) out.push({ label: r.imputed ? "Actual (imputed)" : "Actual", value: fmt1(r.actual), color: ROLE.actual });
              if (r.forecast != null && r.actual == null) out.push({ label: "Forecast", value: fmt1(r.forecast), color: ROLE.forecast });
              if (r.band) out.push({ label: covLabel, value: `${fmt1(r.band[0])} – ${fmt1(r.band[1])}` });
              if (r.adjusted != null) out.push({ label: "With planner override", value: fmt1(r.adjusted), color: ROLE.ai });
              return <TooltipBox title={fmtShortDate(String(label))} rows={out} />;
            }}
          />
          <Legend wrapperStyle={LEGEND_STYLE} />
          {splitDate && <ReferenceLine x={splitDate} stroke="#7c8aa1" strokeDasharray="3 3" label={{ value: "Forecast starts", position: "insideTopLeft", fill: "#94a3b8", fontSize: 11 }} />}
          <Area dataKey="band" name={covLabel} stroke="none" fill={ROLE.forecast} fillOpacity={0.16} isAnimationActive={false} legendType="square" />
          <Line legendType="plainline" dataKey="actual" name="Actual" stroke={ROLE.actual} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
          <Line legendType="plainline" dataKey="forecast" name="Forecast" stroke={ROLE.forecast} strokeWidth={2} strokeDasharray={DASH.forecast} dot={false} isAnimationActive={false} connectNulls />
          {adj.size > 0 && (
            <Line legendType="plainline" dataKey="adjusted" name="With planner override" stroke={ROLE.ai} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

export function TrendChart({ points, label }: { points: { date: string; units: number }[]; label: string }) {
  return (
    <ChartFrame label={label} height={240} table={<SimpleTable cols={["Date", "Units"]} rows={points.map((p) => [p.date, fmt1(p.units)])} />}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={points} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey="date" tickFormatter={fmtShortDate} minTickGap={40} {...AXIS} />
          <YAxis {...AXIS} width={52} />
          <Tooltip content={({ active, payload, label: l }) => (active && payload?.length ? <TooltipBox title={fmtShortDate(String(l))} rows={[{ label: "Units sold", value: fmt1(payload[0]!.value as number), color: ROLE.actual }]} /> : null)} />
          <Area dataKey="units" name="Units sold" stroke={ROLE.actual} strokeWidth={2} fill={ROLE.actual} fillOpacity={0.1} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

export function ScenarioChart({ curve }: { curve: { date: string; baseline: number; scenario: number }[] }) {
  return (
    <ChartFrame label="Baseline versus scenario demand" table={<SimpleTable cols={["Date", "Baseline", "Scenario"]} rows={curve.map((c) => [c.date, fmt1(c.baseline), fmt1(c.scenario)])} />}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={curve} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey="date" tickFormatter={fmtShortDate} minTickGap={30} {...AXIS} />
          <YAxis {...AXIS} width={48} />
          <Tooltip
            content={({ active, payload, label }) =>
              active && payload?.length ? (
                <TooltipBox
                  title={fmtShortDate(String(label))}
                  rows={[
                    { label: "Baseline forecast", value: fmt1(payload[0]?.value as number), color: ROLE.forecast },
                    { label: "Scenario", value: fmt1(payload[1]?.value as number), color: ROLE.ai },
                  ]}
                />
              ) : null
            }
          />
          <Legend wrapperStyle={LEGEND_STYLE} />
          <Line legendType="plainline" dataKey="baseline" name="Baseline forecast" stroke={ROLE.forecast} strokeWidth={2} dot={false} isAnimationActive={false} />
          <Line legendType="plainline" dataKey="scenario" name="Scenario" stroke={ROLE.ai} strokeWidth={2} strokeDasharray={DASH.scenario} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

export function RiskScatter({ rows, onSelect }: { rows: Recommendation[]; onSelect?: (productId: string) => void }) {
  const CAP = 60;
  const levels = ["critical", "high", "medium", "low"] as const;
  const data = rows
    .filter((r) => r.status === "ok")
    .map((r) => ({
      id: r.product_id,
      sku: r.sku,
      level: r.risk_level,
      x: r.days_until_stockout == null ? CAP : Math.min(r.days_until_stockout, CAP),
      y: (r.stockout_probability_lead_time ?? 0) * 100,
      dts: r.days_until_stockout,
      urgency: r.urgency ?? 0,
    }));
  return (
    <ChartFrame
      label="Stockout risk: days of stock versus probability of stockout within lead time"
      height={340}
      table={<SimpleTable cols={["SKU", "Risk", "Days of stock", "P(stockout)", "Urgency"]} rows={data.map((d) => [d.sku, d.level, d.dts == null ? "no demand" : fmt1(d.dts), `${fmt1(d.y)}%`, fmt1(d.urgency)])} />}
    >
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 8, right: 16, bottom: 16, left: -4 }}>
          <CartesianGrid stroke="rgba(148,163,184,0.10)" />
          <XAxis type="number" dataKey="x" name="Days of stock" domain={[0, CAP]} {...AXIS} label={{ value: `Days until stockout (capped at ${CAP})`, position: "insideBottom", offset: -8, fill: "#94a3b8", fontSize: 11 }} />
          <YAxis type="number" dataKey="y" name="P(stockout)" domain={[0, 100]} unit="%" {...AXIS} width={48} />
          <ZAxis range={[70, 70]} />
          <Tooltip
            cursor={{ strokeDasharray: "3 3" }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const d = payload[0]!.payload as (typeof data)[number];
              return (
                <TooltipBox
                  title={d.sku}
                  rows={[
                    { label: "Risk", value: d.level, color: STATUS[d.level as keyof typeof STATUS] },
                    { label: "Days of stock", value: d.dts == null ? "no demand" : fmt1(d.dts) },
                    { label: "P(stockout in lead time)", value: fmtPct(d.y / 100) },
                    { label: "Urgency", value: fmt1(d.urgency) },
                  ]}
                />
              );
            }}
          />
          <Legend verticalAlign="top" wrapperStyle={{ ...LEGEND_STYLE, paddingBottom: 8 }} />
          {levels.map((lvl) => (
            <Scatter
              key={lvl}
              name={lvl[0]!.toUpperCase() + lvl.slice(1)}
              data={data.filter((d) => d.level === lvl)}
              fill={STATUS[lvl]}
              shape={STATUS_SHAPE[lvl]}
              legendType={STATUS_SHAPE[lvl]}
              stroke="#0c111c"
              strokeWidth={2}
              isAnimationActive={false}
              onClick={(p: { id?: string }) => p?.id && onSelect?.(p.id)}
              cursor={onSelect ? "pointer" : undefined}
            />
          ))}
        </ScatterChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

export function ImportanceChart({ features }: { features: { feature: string; delta_mae_mean: number; delta_mae_std: number }[] }) {
  const label = (f: string) => f.replace(/_/g, " ");
  return (
    <ChartFrame label="Permutation importance (increase in MAE when shuffled)" height={Math.max(160, features.length * 40)} table={<SimpleTable cols={["Feature", "ΔMAE", "± sd"]} rows={features.map((f) => [label(f.feature), fmt1(f.delta_mae_mean), fmt1(f.delta_mae_std)])} />}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={features.map((f) => ({ ...f, name: label(f.feature) }))} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 24 }}>
          <CartesianGrid stroke="rgba(148,163,184,0.10)" horizontal={false} />
          <XAxis type="number" {...AXIS} />
          <YAxis type="category" dataKey="name" width={110} {...AXIS} />
          <Tooltip content={({ active, payload }) => (active && payload?.length ? <TooltipBox title={String((payload[0]!.payload as { name: string }).name)} rows={[{ label: "ΔMAE (units/day)", value: fmt1(payload[0]!.value as number) }]} /> : null)} />
          <Bar dataKey="delta_mae_mean" fill={ROLE.ai} radius={[0, 4, 4, 0]} barSize={16} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

export function LossChart({ history }: { history: { epoch: number; train_loss: number; val_loss: number }[] }) {
  return (
    <ChartFrame label="Training and validation loss by epoch" height={200} table={<SimpleTable cols={["Epoch", "Train", "Validation"]} rows={history.map((h) => [h.epoch, h.train_loss, h.val_loss])} />}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={history} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
          <CartesianGrid {...GRID} />
          <XAxis dataKey="epoch" {...AXIS} />
          <YAxis {...AXIS} width={48} />
          <Tooltip content={({ active, payload, label }) => (active && payload?.length ? <TooltipBox title={`Epoch ${label}`} rows={payload.map((p, i) => ({ label: String(p.name), value: Number(p.value).toFixed(4), color: i === 0 ? ROLE.ai : ROLE.aiSoft }))} /> : null)} />
          <Legend wrapperStyle={LEGEND_STYLE} />
          <Line legendType="plainline" dataKey="train_loss" name="Training loss" stroke={ROLE.ai} strokeWidth={2} dot={false} isAnimationActive={false} />
          <Line legendType="plainline" dataKey="val_loss" name="Validation loss" stroke={ROLE.aiSoft} strokeWidth={2} strokeDasharray={DASH.validation} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
