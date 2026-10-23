"use client";

import { useState, type FormEvent } from "react";
import useSWR from "swr";

import { ScenarioChart } from "@/components/charts";
import { ProductPicker } from "@/components/ProductPicker";
import { Button, Card, EmptyState, ErrorState, Field, Input, LoadingBlock, Notice, PageHeader, RiskBadge, Select, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { fmt1, fmtInt, fmtPct } from "@/lib/format";
import type { Recommendation, ScenarioResult, ScenarioTypeSpec } from "@/lib/types";

interface Adj {
  key: number;
  type: string;
  params: Record<string, string>;
}

const label = (t: string) => t.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

const COMPARE: { key: keyof Recommendation; label: string; fmt: (v: number | null | undefined) => string }[] = [
  { key: "mean_daily_demand", label: "Mean daily demand", fmt: fmt1 },
  { key: "lead_time_days", label: "Lead time (days)", fmt: fmt1 },
  { key: "safety_stock", label: "Safety stock", fmt: fmtInt },
  { key: "reorder_point", label: "Reorder point", fmt: fmtInt },
  { key: "recommended_order_qty", label: "Recommended order", fmt: fmtInt },
  { key: "days_until_stockout", label: "Days until stockout", fmt: fmt1 },
  { key: "stockout_probability_lead_time", label: "Stockout risk in lead time", fmt: (v) => fmtPct(v) },
  { key: "urgency", label: "Urgency (0–100)", fmt: fmtInt },
];

export default function ScenariosPage() {
  const types = useSWR<{ types: Record<string, ScenarioTypeSpec> }>("/simulate/types");
  const [productId, setProductId] = useState("");
  const [horizon, setHorizon] = useState(28);
  const [adjs, setAdjs] = useState<Adj[]>([]);
  const [nextKey, setNextKey] = useState(1);
  const [res, setRes] = useState<ScenarioResult | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const spec = types.data?.types ?? {};
  const add = (type: string) => {
    const s = spec[type];
    if (!s) return;
    setAdjs([...adjs, { key: nextKey, type, params: Object.fromEntries(Object.entries(s.params).map(([k, v]) => [k, String(v.default)])) }]);
    setNextKey(nextKey + 1);
  };

  const run = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    if (!productId) return setErr(new Error("Choose a product"));
    if (!adjs.length) return setErr(new Error("Add at least one adjustment"));
    const adjustments = [];
    for (const a of adjs) {
      const params: Record<string, number> = {};
      for (const [k, v] of Object.entries(a.params)) {
        const n = Number(v);
        const lim = spec[a.type]?.params[k];
        if (!Number.isFinite(n) || (lim && (n < lim.min || n > lim.max))) {
          return setErr(new Error(`${label(a.type)}: ${k.replace(/_/g, " ")} must be between ${lim?.min} and ${lim?.max}`));
        }
        params[k] = n;
      }
      adjustments.push({ type: a.type, params });
    }
    setBusy(true);
    try {
      setRes(await api<ScenarioResult>("/simulate/scenario", { method: "POST", body: { product_id: productId, adjustments, horizon } }));
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Scenarios"
        description="Test what-if changes to demand, lead time or service level and see how the reorder recommendation responds. Nothing here changes stored data."
      />
      <div className="grid gap-6 xl:grid-cols-3">
        <Card title="Set up" className="xl:col-span-1">
          {types.error ? (
            <ErrorState error={types.error} />
          ) : !types.data ? (
            <LoadingBlock />
          ) : (
            <form onSubmit={run} className="space-y-4">
              <Field label="Product" htmlFor="sp">
                <ProductPicker id="sp" value={productId} onChange={setProductId} />
              </Field>
              <Field label="Horizon (days)" htmlFor="sh">
                <Select id="sh" value={horizon} onChange={(e) => setHorizon(Number(e.target.value))}>
                  {[14, 28, 56, 90].map((h) => <option key={h} value={h}>{h}</option>)}
                </Select>
              </Field>
              <div className="space-y-3">
                {adjs.map((a) => (
                  <fieldset key={a.key} className="rounded-lg border border-line p-3">
                    <legend className="px-1 text-sm font-medium text-ink">{label(a.type)}</legend>
                    <p className="mb-2 text-xs text-faint">{spec[a.type]?.description}</p>
                    <div className="grid grid-cols-2 gap-2">
                      {Object.entries(spec[a.type]?.params ?? {}).map(([k, lim]) => (
                        <Field key={k} label={`${k.replace(/_/g, " ")} (${lim.min}–${lim.max})`} htmlFor={`a${a.key}-${k}`}>
                          <Input
                            id={`a${a.key}-${k}`}
                            inputMode="decimal"
                            value={a.params[k] ?? ""}
                            onChange={(e) => setAdjs(adjs.map((x) => (x.key === a.key ? { ...x, params: { ...x.params, [k]: e.target.value } } : x)))}
                          />
                        </Field>
                      ))}
                    </div>
                    <Button variant="ghost" className="mt-2" onClick={() => setAdjs(adjs.filter((x) => x.key !== a.key))}>Remove</Button>
                  </fieldset>
                ))}
              </div>
              {adjs.length < 5 && (
                <Field label="Add adjustment" htmlFor="addadj">
                  <Select
                    id="addadj"
                    value=""
                    onChange={(e) => {
                      add(e.target.value);
                    }}
                  >
                    <option value="">Choose…</option>
                    {Object.keys(spec).map((t) => <option key={t} value={t}>{label(t)}</option>)}
                  </Select>
                </Field>
              )}
              {err != null && <ErrorState error={err} />}
              <Button type="submit" variant="primary" loading={busy}>Run scenario</Button>
            </form>
          )}
        </Card>

        <Card title="Result" className="xl:col-span-2">
          {!res ? (
            <EmptyState title="No scenario yet">Pick a product, add one or more adjustments and run the scenario.</EmptyState>
          ) : res.status !== "ok" || !res.scenario ? (
            <Notice tone="warn">{res.baseline.message ?? "A scenario needs a forecast and a known on-hand count for this product."}</Notice>
          ) : (
            <>
              {res.curve && <ScenarioChart curve={res.curve} />}
              <div className="mt-4">
                <Table caption="Baseline versus scenario">
                  <thead>
                    <tr>
                      <Th>Measure</Th>
                      <Th className="text-right">Baseline</Th>
                      <Th className="text-right">Scenario</Th>
                      <Th className="text-right">Change</Th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <Td>Risk level</Td>
                      <Td className="text-right"><RiskBadge level={res.baseline.risk_level} /></Td>
                      <Td className="text-right"><RiskBadge level={res.scenario.risk_level} /></Td>
                      <Td />
                    </tr>
                    {COMPARE.map((c) => {
                      const b = res.baseline[c.key] as number | null | undefined;
                      const s = res.scenario?.[c.key] as number | null | undefined;
                      const d = b != null && s != null ? s - b : null;
                      return (
                        <tr key={c.key}>
                          <Td>{c.label}</Td>
                          <Td className="text-right tabular-nums">{c.fmt(b)}</Td>
                          <Td className="text-right tabular-nums">{c.fmt(s)}</Td>
                          <Td className="text-right tabular-nums">{d == null || Math.abs(d) < 1e-9 ? "—" : `${d > 0 ? "+" : "−"}${c.fmt(Math.abs(d))}`}</Td>
                        </tr>
                      );
                    })}
                  </tbody>
                </Table>
              </div>
              {res.adjustments?.some((a) => a.signals_applied?.length) && (
                <p className="mt-3 text-xs text-faint">
                  Signals applied: {res.adjustments.flatMap((a) => a.signals_applied ?? []).join(", ")}
                </p>
              )}
            </>
          )}
        </Card>
      </div>
    </>
  );
}
