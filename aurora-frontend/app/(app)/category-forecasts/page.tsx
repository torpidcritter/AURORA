"use client";

import { useState, type FormEvent } from "react";
import useSWR from "swr";

import { HierarchyResult } from "@/components/HierarchyResult";
import { Button, Card, ErrorState, Field, LoadingBlock, PageHeader, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { hierarchyBody, RECONCILE_LABEL, scopeValue, TOTAL, type Reconcile } from "@/lib/hierarchy";
import type { HierarchyCategory, HierarchyForecast } from "@/lib/types";

export default function CategoryForecastsPage() {
  const cats = useSWR<HierarchyCategory[]>("/forecast/hierarchy/categories");
  const [scope, setScope] = useState(TOTAL);
  const [horizon, setHorizon] = useState(28);
  const [reconcile, setReconcile] = useState<Reconcile>("auto");
  const [result, setResult] = useState<HierarchyForecast | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const run = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      setResult(await api<HierarchyForecast>("/forecast/hierarchy", { method: "POST", body: hierarchyBody(scope, horizon, reconcile) }));
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Category forecasts"
        description="Forecast a whole category or the business, with product numbers that add up to the total. Aurora compares summing the product forecasts with a model of the group's own sales, and uses whichever was more accurate on past data unless you choose."
      />
      <Card className="mb-6">
        {cats.error ? (
          <ErrorState error={cats.error} onRetry={() => cats.mutate()} />
        ) : !cats.data ? (
          <LoadingBlock rows={1} />
        ) : (
          <form onSubmit={run} className="grid gap-3 md:grid-cols-4" aria-label="Category forecast">
            <Field label="Group" htmlFor="hs">
              <Select id="hs" value={scope} onChange={(e) => setScope(e.target.value)}>
                <option value={TOTAL}>All products</option>
                {cats.data.map((c) => (
                  <option key={scopeValue(c.category)} value={scopeValue(c.category)}>
                    {c.label} ({c.products} products)
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Horizon (days)" htmlFor="hh">
              <Select id="hh" value={horizon} onChange={(e) => setHorizon(Number(e.target.value))}>
                {[7, 14, 28, 56, 90].map((h) => <option key={h} value={h}>{h}</option>)}
              </Select>
            </Field>
            <Field label="Method" htmlFor="hr">
              <Select id="hr" value={reconcile} onChange={(e) => setReconcile(e.target.value as Reconcile)}>
                <option value="auto">Most accurate on past data</option>
                <option value="bottom_up">{RECONCILE_LABEL.bottom_up}</option>
                <option value="top_down">{RECONCILE_LABEL.top_down}</option>
              </Select>
            </Field>
            <div className="flex items-end"><Button type="submit" variant="primary" loading={busy}>Forecast group</Button></div>
          </form>
        )}
        {busy && <p className="mt-3 text-xs text-muted">Forecasting each product and backtesting both methods. Large groups take up to a minute.</p>}
        {err != null && <div className="mt-3"><ErrorState error={err} /></div>}
      </Card>
      {result && <HierarchyResult f={result} />}
    </>
  );
}
