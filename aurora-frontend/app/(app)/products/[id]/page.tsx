"use client";

import Link from "next/link";
import { use, useState } from "react";
import useSWR from "swr";

import { DemandForecastChart } from "@/components/charts";
import { InventoryEditor } from "@/components/InventoryEditor";
import { OverridesCard } from "@/components/OverridesCard";
import { PriceBreaksEditor } from "@/components/PriceBreaksEditor";
import { StockByLocation } from "@/components/StockByLocation";
import { SupplierPicker } from "@/components/SupplierPicker";
import { RecommendationPanel } from "@/components/RecommendationPanel";
import { Badge, Button, Card, EmptyState, ErrorState, Field, LoadingBlock, Notice, PageHeader, Select, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { fmt1, fmt2, fmtDate, fmtInt, fmtPct, fmtPctPoints, methodLabel } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Explanation, Forecast, HistoryPoint, Product, Recommendation, StockLocation } from "@/lib/types";

const METHODS = ["auto", "transformer", "holt_winters", "ses", "sma", "seasonal_naive", "croston_sba"];

export default function ProductDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { can } = useSession();
  const product = useSWR<Product>(`/products/${id}`);
  const history = useSWR<{ points: HistoryPoint[]; missing_policy: string }>(`/products/${id}/history?days=180`);
  const explain = useSWR<Explanation>(`/forecast/explain/${id}`, { shouldRetryOnError: false });
  const locations = useSWR<StockLocation[]>("/locations");
  const multiLocation = (locations.data?.length ?? 0) > 1;

  const [method, setMethod] = useState("auto");
  const [horizon, setHorizon] = useState(28);
  const [coverage, setCoverage] = useState(0.9);
  // The forecast request that was last submitted; the form fields only take effect on "Run forecast".
  const [req, setReq] = useState({ method: "auto", horizon: 28, coverage: 0.9, n: 0 });
  const once = { revalidateOnFocus: false, revalidateIfStale: false, revalidateOnReconnect: false, shouldRetryOnError: false };
  const forecast = useSWR<Forecast>(
    ["forecast", id, req.method, req.horizon, req.coverage, req.n],
    () => api<Forecast>("/forecast", { method: "POST", body: { product_id: id, horizon: req.horizon, method: req.method, coverage: req.coverage } }),
    { ...once, keepPreviousData: true },
  );
  const recommendation = useSWR<Recommendation>(
    ["recommendation", id],
    () => api<Recommendation>("/inventory/recommendation", { method: "POST", body: { product_id: id } }),
    once,
  );
  const fc = forecast.data ?? null;
  const fcErr: unknown = forecast.error ?? null;
  const fcBusy = forecast.isValidating;
  const rec = recommendation.data ?? null;
  const recErr: unknown = recommendation.error ?? null;
  const runForecast = () => setReq((r) => ({ method, horizon, coverage, n: r.n + 1 }));
  const loadRec = () => recommendation.mutate();

  if (product.error) return <ErrorState error={product.error} onRetry={() => product.mutate()} />;
  const p = product.data;
  if (!p) return <LoadingBlock rows={10} label="Loading product" />;

  const bt = fc?.backtest;
  return (
    <>
      <div className="mb-2 text-sm"><Link href="/products" className="text-primary hover:underline">Products</Link> / {p.sku}</div>
      <PageHeader
        title={`${p.sku} · ${p.name}`}
        description={`${p.category ?? "Uncategorised"} · ${p.history_days} days of history (${fmtDate(p.first_date)} to ${fmtDate(p.last_date)}) · missing days treated as ${p.missing_policy === "zero" ? "zero sales" : "unknown (interpolated)"}`}
      />

      <Card
        title="Demand and forecast"
        subtitle={fc ? `${methodLabel(fc.method)} from ${fmtDate(fc.as_of)}. ${fc.method_reason}` : "Observed daily units and the forecast with its prediction interval"}
      >
        <form
          className="mb-4 flex flex-wrap items-end gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            runForecast();
          }}
        >
          <Field label="Method" htmlFor="fm">
            <Select id="fm" value={method} onChange={(e) => setMethod(e.target.value)}>
              {METHODS.map((m) => <option key={m} value={m}>{m === "auto" ? "Auto (best on held-out data)" : methodLabel(m)}</option>)}
            </Select>
          </Field>
          <Field label="Horizon (days)" htmlFor="fh">
            <Select id="fh" value={horizon} onChange={(e) => setHorizon(Number(e.target.value))}>
              {[7, 14, 28, 56, 90].map((h) => <option key={h} value={h}>{h}</option>)}
            </Select>
          </Field>
          <Field label="Interval" htmlFor="fcv">
            <Select id="fcv" value={coverage} onChange={(e) => setCoverage(Number(e.target.value))}>
              {[0.8, 0.9, 0.95].map((c) => <option key={c} value={c}>{fmtPct(c)}</option>)}
            </Select>
          </Field>
          <Button type="submit" loading={fcBusy}>Run forecast</Button>
        </form>
        {fcErr != null && <div className="mb-3"><ErrorState error={fcErr} /></div>}
        {history.error ? (
          <ErrorState error={history.error} onRetry={() => history.mutate()} />
        ) : !history.data ? (
          <LoadingBlock rows={6} />
        ) : history.data.points.length === 0 ? (
          <EmptyState title="No sales history for this product" />
        ) : (
          <DemandForecastChart history={history.data.points} forecast={fc?.points} coverage={fc?.interval.coverage} adjusted={fc?.adjusted_points} />
        )}
        {fc && (
          <div className="mt-4 space-y-2 text-sm text-muted">
            <p>
              <Badge tone={fc.interval.calibrated ? "ok" : "warn"}>{fc.interval.calibrated ? "Calibrated interval" : "Uncalibrated interval"}</Badge>{" "}
              {fmtPct(fc.interval.coverage)} nominal
              {fc.interval.empirical_coverage != null && <> · {fmtPct(fc.interval.empirical_coverage, 1)} measured on {fc.interval.n_test} held-out points</>}. {fc.interval.note}
            </p>
            {fc.epistemic_uncertainty && (
              <p>
                Model uncertainty (MC dropout, {fc.epistemic_uncertainty.samples} samples): average spread ±{fmt2(avg(fc.epistemic_uncertainty.std))} units/day. {fc.epistemic_uncertainty.note}
              </p>
            )}
            <p>Transformer: {fc.transformer_status}{fc.model_version ? ` (model ${fc.model_version})` : ""}.</p>
            {fc.overrides?.length ? (
              <Notice tone="info">
                A planner override covers {fc.overrides.reduce((n, o) => n + o.days, 0)} day(s) of this horizon ({fc.overrides.map((o) => o.reason).join("; ")}). The purple line is the demand used for reorder decisions; the dashed line is the model.
              </Notice>
            ) : null}
            {fc.warnings.map((w) => <Notice key={w} tone="warn">{w}</Notice>)}
            {fc.signals.length > 0 && (
              <Notice tone="info">
                {fc.signals.length} external signal(s) overlap this horizon ({fc.signals.map((s) => s.name).join(", ")}). They are shown for context and are not applied to the forecast. Use Scenarios to test their effect.
              </Notice>
            )}
          </div>
        )}
      </Card>

      <div className="mt-6 grid gap-6 xl:grid-cols-2">
        <Card title="Backtest" subtitle={bt ? `Rolling-origin evaluation, ${bt.origins} origins at this horizon. Lower is better.` : "Accuracy of each method on past data"}>
          {!fc ? (
            <LoadingBlock />
          ) : !bt ? (
            <EmptyState title="Not enough history to backtest" />
          ) : bt.error ? (
            <Notice tone="warn">{bt.error}</Notice>
          ) : (
            <Table caption="Backtest metrics by method">
              <thead>
                <tr>
                  <Th>Method</Th>
                  <Th className="text-right">WAPE</Th>
                  <Th className="text-right">MAE</Th>
                  <Th className="text-right">RMSE</Th>
                  <Th className="text-right" >MAPE*</Th>
                  <Th className="text-right">Bias</Th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(bt.methods)
                  .sort((a, b) => (a[1].wape ?? Infinity) - (b[1].wape ?? Infinity))
                  .map(([m, v]) => (
                    <tr key={m}>
                      <Td>
                        {methodLabel(m)} {m === bt.best_method && <Badge tone="ok">best</Badge>}
                      </Td>
                      <Td className="text-right tabular-nums">{fmtPctPoints(v.wape)}</Td>
                      <Td className="text-right tabular-nums">{fmt2(v.mae)}</Td>
                      <Td className="text-right tabular-nums">{fmt2(v.rmse)}</Td>
                      <Td className="text-right tabular-nums">{fmtPctPoints(v.mape)}</Td>
                      <Td className="text-right tabular-nums">{fmt2(v.bias)}</Td>
                    </tr>
                  ))}
              </tbody>
            </Table>
          )}
          {bt && !bt.error && (
            <p className="mt-2 text-xs text-faint">*MAPE excludes days with zero actual sales, so it can look better than WAPE on intermittent items.</p>
          )}
        </Card>

        <Card title="What drives this demand" subtitle="Descriptive statistics of the history, not a causal model">
          {explain.error ? (
            <ErrorState error={explain.error} />
          ) : !explain.data ? (
            <LoadingBlock />
          ) : (
            <>
              <ul className="space-y-2 text-sm">
                {explain.data.drivers.map((d) => (
                  <li key={d.driver} className="flex gap-2">
                    {d.significant ? <Badge tone="info">notable</Badge> : <Badge>info</Badge>}
                    <span className="text-ink">{d.text}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-xs text-faint">{explain.data.method_note}</p>
            </>
          )}
        </Card>

        <Card title="Inventory recommendation" subtitle="Safety stock, reorder point and order quantity from the forecast">
          {recErr != null ? <ErrorState error={recErr} onRetry={() => void loadRec()} /> : !rec ? <LoadingBlock /> : <RecommendationPanel rec={rec} />}
        </Card>

        <Card title="Stock settings" subtitle={can("manage_inventory") ? "Changes are audited" : "Read only for your role"}>
          <InventoryEditor
            key={`${p.id}-${multiLocation}`}
            product={p}
            stockByLocation={multiLocation}
            readOnly={!can("manage_inventory")}
            onSaved={async (updated) => {
              await product.mutate(updated, { revalidate: false });
              await loadRec();
            }}
          />
          <p className="mt-3 text-xs text-faint">Units on hand: {p.inventory_known ? fmtInt(p.on_hand) : "unknown"} · on order {fmtInt(p.on_order)} · 30-day average {fmt1(p.avg_daily_30d)}/day</p>
          <div className="mt-4 border-t border-line pt-4">
            <SupplierPicker
              product={p}
              editable={can("manage_purchasing")}
              onSaved={async () => {
                await product.mutate();
                await loadRec();
              }}
            />
          </div>
          <div className="mt-4 border-t border-line pt-4">
            <h3 className="mb-2 text-sm font-medium text-ink">Price breaks</h3>
            <PriceBreaksEditor
              key={`${p.id}-${p.version}`}
              product={p}
              editable={can("manage_purchasing")}
              onSaved={async () => {
                await product.mutate();
                await loadRec();
              }}
            />
          </div>
        </Card>

        <Card title="Stock by location" subtitle={multiLocation ? "Count or move stock at each location" : "Add locations to track stock in more than one place"}>
          <StockByLocation
            product={p}
            editable={can("manage_inventory")}
            onChanged={async () => {
              await product.mutate();
              await locations.mutate();
              await loadRec();
            }}
          />
        </Card>

        <OverridesCard
          productId={p.id}
          editable={can("manage_overrides")}
          onChanged={async () => {
            await loadRec();
            runForecast();
          }}
        />
      </div>
    </>
  );
}

function avg(xs: number[]): number {
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0;
}
