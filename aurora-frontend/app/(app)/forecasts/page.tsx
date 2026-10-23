"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";

import { JobProgress, TERMINAL, useJob } from "@/components/JobProgress";
import { Badge, Button, Card, EmptyState, ErrorState, Field, LoadingBlock, PageHeader, Select, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDate, fmtDateTime, fmtInt, fmtPct, methodLabel } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Job } from "@/lib/types";

interface LatestRow {
  product_id: string;
  sku: string;
  name: string;
  method: string;
  horizon: number;
  coverage: number;
  created_at: string;
  as_of: string | null;
  next_7d_units: number;
  interval_calibrated: boolean | null;
  empirical_coverage: number | null;
}

export default function ForecastsPage() {
  const { can } = useSession();
  const latest = useSWR<{ items: LatestRow[] }>("/forecast/latest?limit=500");
  const [horizon, setHorizon] = useState(28);
  const [jobId, setJobId] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const job = useJob(jobId);

  const start = async () => {
    setErr(null);
    setBusy(true);
    try {
      const j = await api<Job>(`/jobs/batch-forecast?horizon=${horizon}`, { method: "POST", headers: { "idempotency-key": crypto.randomUUID() } });
      setJobId(j.id);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Forecasts"
        description="The most recent stored forecast for each product. Open a product to run an interactive forecast with any method."
      />
      {can("train_models") && (
        <Card title="Forecast all products" subtitle="Runs in the background and stores a forecast per product using the Auto method" className="mb-6">
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Horizon (days)" htmlFor="bh">
              <Select id="bh" value={horizon} onChange={(e) => setHorizon(Number(e.target.value))}>
                {[7, 14, 28, 56, 90].map((h) => <option key={h} value={h}>{h}</option>)}
              </Select>
            </Field>
            <Button variant="primary" onClick={start} loading={busy} disabled={!!job.data && !TERMINAL.has(job.data.status)}>
              Start batch forecast
            </Button>
          </div>
          {err != null && <div className="mt-3"><ErrorState error={err} /></div>}
          {job.data && (
            <div className="mt-3">
              <JobProgress job={job.data} canCancel onChanged={() => job.mutate()} />
              {job.data.status === "succeeded" && (
                <Button className="mt-2" onClick={() => latest.mutate()}>Refresh table</Button>
              )}
            </div>
          )}
        </Card>
      )}
      <Card title="Latest forecasts">
        {latest.error ? (
          <ErrorState error={latest.error} onRetry={() => latest.mutate()} />
        ) : !latest.data ? (
          <LoadingBlock rows={8} />
        ) : latest.data.items.length === 0 ? (
          <EmptyState title="No forecasts stored yet">
            Open a <Link href="/products" className="text-primary hover:underline">product</Link> to run one{can("train_models") ? ", or start a batch forecast above" : ""}.
          </EmptyState>
        ) : (
          <Table caption="Latest forecast per product">
            <thead>
              <tr>
                <Th>SKU</Th>
                <Th>Name</Th>
                <Th>Method</Th>
                <Th className="text-right">Next 7 days (units)</Th>
                <Th className="text-right">Horizon</Th>
                <Th>Interval</Th>
                <Th>Data as of</Th>
                <Th>Created</Th>
              </tr>
            </thead>
            <tbody>
              {latest.data.items.map((r) => (
                <tr key={r.product_id} className="hover:bg-white/5">
                  <Td><Link className="font-medium text-primary hover:underline" href={`/products/${r.product_id}`}>{r.sku}</Link></Td>
                  <Td>{r.name}</Td>
                  <Td>{methodLabel(r.method)}</Td>
                  <Td className="text-right tabular-nums">{fmtInt(r.next_7d_units)}</Td>
                  <Td className="text-right tabular-nums">{r.horizon} d</Td>
                  <Td>
                    {fmtPct(r.coverage)}{" "}
                    {r.interval_calibrated ? (
                      <Badge tone="ok">{r.empirical_coverage == null ? "calibrated" : `measured ${fmtPct(r.empirical_coverage)}`}</Badge>
                    ) : (
                      <Badge tone="warn">uncalibrated</Badge>
                    )}
                  </Td>
                  <Td>{fmtDate(r.as_of)}</Td>
                  <Td>{fmtDateTime(r.created_at)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
