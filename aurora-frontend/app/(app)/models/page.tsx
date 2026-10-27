"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";

import { ImportanceChart, LossChart } from "@/components/charts";
import { JobProgress, TERMINAL } from "@/components/JobProgress";
import { LiveAccuracyCard } from "@/components/LiveAccuracyCard";
import { TrainForm, useTrainingJob } from "@/components/TrainForm";
import { Badge, Button, Card, ConfirmButton, EmptyState, ErrorState, LoadingBlock, Notice, PageHeader, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { fmt2, fmtDateTime, fmtPct, fmtPctPoints, methodLabel } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Diagnostics, DriftRow, MethodMetrics, ModelVersion } from "@/lib/types";

const statusTone = (s: ModelVersion["status"]) => (s === "active" ? "ok" : s === "rejected" ? "bad" : s === "candidate" ? "info" : "default");

export default function ModelsPage() {
  const { can } = useSession();
  const registry = useSWR<{ items: ModelVersion[] }>("/models/registry");
  const drift = useSWR<{ items: DriftRow[]; drifting: number }>("/intelligence/drift");
  const [selected, setSelected] = useState<string>("active");
  const diag = useSWR<Diagnostics>(`/intelligence/diagnostics/${selected}`, { shouldRetryOnError: false });

  const [jobId, setJobId] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<unknown>(null);
  // Resume tracking a training job that is still running (e.g. after a page reload).
  const { job, refreshRecent } = useTrainingJob(jobId);

  const done = job.data && TERMINAL.has(job.data.status);
  useEffect(() => {
    if (done) {
      void registry.mutate();
      void diag.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [done]);

  const promote = async (m: ModelVersion, force = false) => {
    setActionErr(null);
    await api(`/models/registry/${m.id}/promote`, { method: "POST", body: { force } });
    await registry.mutate();
    await diag.mutate();
  };

  const overall = diag.data?.overall;
  const methodRows = overall
    ? Object.entries(overall).filter((e): e is [string, MethodMetrics] => !Array.isArray(e[1]) && e[1] != null)
    : [];

  return (
    <>
      <PageHeader
        title="Models & drift"
        description="Train the Transformer, compare it honestly with statistical baselines, and control which version is active."
      />
      {can("train_models") && (
        <Card title="Train a new version" subtitle="Training runs in the background. A version is promoted only if it passes the quality gate." className="mb-6">
          <TrainForm onStarted={(j) => setJobId(j.id)} running={!!job.data && !TERMINAL.has(job.data.status)} onError={() => void refreshRecent()} />
          {job.data && <div className="mt-3"><JobProgress job={job.data} canCancel onChanged={() => job.mutate()} /></div>}
        </Card>
      )}

      <LiveAccuracyCard className="mb-6" />

      <Card
        title="Model registry"
        className="mb-6"
        actions={
          can("manage_models") && (registry.data?.items.some((m) => m.status === "archived") ?? false) ? (
            <ConfirmButton
              variant="secondary"
              title="Roll back the active model?"
              body="The previously active version becomes active again. Forecasts that use the Transformer switch immediately."
              confirmLabel="Roll back"
              onConfirm={async () => {
                await api("/models/registry/rollback", { method: "POST" });
                await registry.mutate();
                await diag.mutate();
              }}
            >
              Roll back
            </ConfirmButton>
          ) : null
        }
      >
        {actionErr != null && <div className="mb-3"><ErrorState error={actionErr} /></div>}
        {registry.error ? (
          <ErrorState error={registry.error} onRetry={() => registry.mutate()} />
        ) : !registry.data ? (
          <LoadingBlock />
        ) : registry.data.items.length === 0 ? (
          <EmptyState title="No trained models">Forecasts use statistical methods until a Transformer version passes the gate.</EmptyState>
        ) : (
          <Table caption="Model versions">
            <thead>
              <tr>
                <Th>Version</Th>
                <Th>Status</Th>
                <Th className="text-right">Transformer WAPE</Th>
                <Th className="text-right">Best baseline WAPE</Th>
                <Th>Gate</Th>
                <Th className="text-right">Products</Th>
                <Th>Created</Th>
                <Th><span className="sr-only">Actions</span></Th>
              </tr>
            </thead>
            <tbody>
              {registry.data.items.map((m) => (
                <tr key={m.id} className={selected === m.id ? "bg-white/5" : undefined}>
                  <Td><span className="font-mono text-xs">{m.version}</span></Td>
                  <Td><Badge tone={statusTone(m.status)}>{m.status}</Badge></Td>
                  <Td className="text-right tabular-nums">{fmtPctPoints(m.transformer_wape)}</Td>
                  <Td className="text-right tabular-nums">
                    {fmtPctPoints(m.best_baseline_wape)} <span className="text-xs text-faint">{methodLabel(m.best_baseline)}</span>
                  </Td>
                  <Td>
                    <span title={m.gate_reason ?? undefined}>
                      <Badge tone={m.gate_passed ? "ok" : "warn"}>{m.gate_passed ? "passed" : "failed"}</Badge>
                    </span>
                  </Td>
                  <Td className="text-right tabular-nums">{m.products}</Td>
                  <Td>{fmtDateTime(m.created_at)}</Td>
                  <Td className="whitespace-nowrap text-right">
                    <Button variant="ghost" onClick={() => setSelected(m.id)}>Diagnostics</Button>
                    {can("manage_models") && m.status !== "active" && (
                      <ConfirmButton
                        variant="secondary"
                        title={m.gate_passed ? `Promote ${m.version}?` : `Force-promote ${m.version}?`}
                        body={
                          m.gate_passed
                            ? "This version becomes active and the current one is archived."
                            : `This version failed the quality gate (${m.gate_reason ?? "see diagnostics"}). Promoting it anyway is recorded in the audit log.`
                        }
                        confirmLabel={m.gate_passed ? "Promote" : "Force promote"}
                        onConfirm={() => promote(m, !m.gate_passed || m.status === "rejected")}
                      >
                        Promote
                      </ConfirmButton>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <Card title={selected === "active" ? "Active model diagnostics" : "Diagnostics"} subtitle={diag.data?.evaluation_segment ? `Evaluated on the ${diag.data.evaluation_segment}` : undefined} className="mb-6">
        {diag.error ? (
          <EmptyState title={selected === "active" ? "No active model" : "Diagnostics unavailable"}>
            {selected === "active" ? "Pick a version above to inspect it." : <ErrorState error={diag.error} />}
          </EmptyState>
        ) : !diag.data ? (
          <LoadingBlock rows={6} />
        ) : (
          <div className="grid gap-6 xl:grid-cols-2">
            <div>
              <h3 className="mb-2 text-sm font-medium text-ink">Accuracy on held-out data ({diag.data.model.version})</h3>
              <Table caption="Held-out accuracy by method">
                <thead>
                  <tr>
                    <Th>Method</Th>
                    <Th className="text-right">WAPE</Th>
                    <Th className="text-right">MAE</Th>
                    <Th className="text-right">RMSE</Th>
                  </tr>
                </thead>
                <tbody>
                  {methodRows
                    .sort((a, b) => (a[1].wape ?? Infinity) - (b[1].wape ?? Infinity))
                    .map(([m, v]) => (
                      <tr key={m}>
                        <Td>{methodLabel(m)}</Td>
                        <Td className="text-right tabular-nums">{fmtPctPoints(v.wape)}</Td>
                        <Td className="text-right tabular-nums">{fmt2(v.mae)}</Td>
                        <Td className="text-right tabular-nums">{fmt2(v.rmse)}</Td>
                      </tr>
                    ))}
                </tbody>
              </Table>
              {diag.data.per_product && (
                <p className="mt-2 text-sm text-muted">
                  The Transformer beat the best baseline on {Object.values(diag.data.per_product).filter((x) => x.transformer_beats_best_baseline).length} of{" "}
                  {Object.keys(diag.data.per_product).length} products. Auto forecasts use it only for those products.
                </p>
              )}
              {diag.data.interval_coverage && (
                <div className="mt-4">
                  <h3 className="mb-2 text-sm font-medium text-ink">Prediction interval coverage</h3>
                  <ul className="space-y-1 text-sm text-muted">
                    {Object.entries(diag.data.interval_coverage).map(([nominal, emp]) => (
                      <li key={nominal}>
                        {fmtPct(Number(nominal))} nominal → {emp == null ? "not measured" : `${fmtPct(emp, 1)} measured`}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
            <div className="space-y-4">
              {diag.data.feature_importance?.features?.length ? (
                <div>
                  <h3 className="mb-2 text-sm font-medium text-ink">Permutation importance</h3>
                  <ImportanceChart features={diag.data.feature_importance.features} />
                  <p className="mt-1 text-xs text-faint">How much MAE grows (units/day) when one input is shuffled. This is permutation importance, not SHAP.</p>
                </div>
              ) : null}
              {diag.data.training.history?.length ? (
                <div>
                  <h3 className="mb-2 text-sm font-medium text-ink">Training curve ({diag.data.training.epochs_run} epochs, {Math.round(diag.data.training.duration_s ?? 0)} s)</h3>
                  <LossChart history={diag.data.training.history} />
                </div>
              ) : null}
            </div>
          </div>
        )}
      </Card>

      <Card
        title="Demand drift"
        subtitle="Compares the last 30 days with up to 180 prior days using a Kolmogorov–Smirnov test and the change in mean"
        actions={
          can("train_models") ? (
            <Button
              variant="secondary"
              onClick={async () => {
                try {
                  await api("/jobs/monitor", { method: "POST" });
                } catch (e) {
                  setActionErr(e);
                }
              }}
            >
              Run monitoring now
            </Button>
          ) : null
        }
      >
        {drift.error ? (
          <ErrorState error={drift.error} onRetry={() => drift.mutate()} />
        ) : !drift.data ? (
          <LoadingBlock />
        ) : drift.data.items.length === 0 ? (
          <EmptyState title="No products to check" />
        ) : (
          <>
            <div className="mb-3">
              <Notice tone={drift.data.drifting ? "warn" : "ok"}>
                {drift.data.drifting
                  ? `${drift.data.drifting} product(s) show drift. Consider retraining; Aurora never retrains or promotes automatically because of drift.`
                  : "No drift detected."}
              </Notice>
            </div>
            <Table caption="Drift by product">
              <thead>
                <tr>
                  <Th>SKU</Th>
                  <Th>Status</Th>
                  <Th className="text-right">KS statistic</Th>
                  <Th className="text-right">p-value</Th>
                  <Th className="text-right">Mean change</Th>
                  <Th>Reason</Th>
                </tr>
              </thead>
              <tbody>
                {drift.data.items.slice(0, 100).map((d) => (
                  <tr key={d.product_id}>
                    <Td>{d.sku}</Td>
                    <Td>{d.drift ? <Badge tone="warn">drift</Badge> : <Badge>{d.status.replace(/_/g, " ")}</Badge>}</Td>
                    <Td className="text-right tabular-nums">{fmt2(d.ks_statistic)}</Td>
                    <Td className="text-right tabular-nums">{d.ks_pvalue == null ? "—" : d.ks_pvalue < 0.001 ? "<0.001" : fmt2(d.ks_pvalue)}</Td>
                    <Td className="text-right tabular-nums">{d.mean_change_ratio == null ? "—" : fmtPct(d.mean_change_ratio)}</Td>
                    <Td className="text-xs text-muted">{(d.reasons ?? (d.reason ? [d.reason] : [])).join("; ") || "—"}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </>
        )}
      </Card>
    </>
  );
}
