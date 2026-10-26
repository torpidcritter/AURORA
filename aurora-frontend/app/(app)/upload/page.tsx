"use client";

import clsx from "clsx";
import { Check, Lock } from "lucide-react";
import Link from "next/link";
import { useRef, useState, type FormEvent } from "react";
import useSWR from "swr";

import { JobProgress, TERMINAL } from "@/components/JobProgress";
import { TrainForm, useTrainingJob } from "@/components/TrainForm";
import { Badge, Button, Card, EmptyState, ErrorState, Field, Input, LoadingBlock, Notice, PageHeader, Pagination, Select, Table, Td, Th } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { fmtBytes, fmtDateTime, fmtInt } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Dataset, Page } from "@/lib/types";
import { MAX_UPLOAD_MB as MAX_MB, validateFile } from "@/lib/validation";
import { trainGate, wizardSteps, type StepState, type TrainGate, type WizardStep } from "@/lib/wizard";

const ACCEPT = ".csv,.tsv,.txt,.xlsx,.json";

function QualityReport({ ds }: { ds: Dataset }) {
  const q = ds.quality_report;
  if (ds.status === "rejected") {
    return (
      <Notice tone="bad">
        <strong>Rejected:</strong> {q.rejection_reason ?? "no valid rows"}
        {q.details && typeof q.details === "object" && "accepted_names" in (q.details as object) ? (
          <div className="mt-1 text-xs">
            Accepted column names:{" "}
            {Object.entries((q.details as { accepted_names: Record<string, string[]> }).accepted_names)
              .map(([k, v]) => `${k} (${v.slice(0, 5).join(", ")})`)
              .join("; ")}
          </div>
        ) : null}
      </Notice>
    );
  }
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric label="Quality score" value={`${ds.quality_score.toFixed(1)} / 100`} />
        <Metric label="Rows accepted" value={`${fmtInt(ds.accepted_rows)} of ${fmtInt(ds.row_count)}`} />
        <Metric label="Products" value={fmtInt(q.products)} />
        <Metric label="Date range" value={q.date_min ? `${q.date_min} → ${q.date_max}` : "—"} />
        <Metric label="Rejected rows" value={fmtInt(ds.rejected_rows)} />
        <Metric label="Duplicates summed" value={fmtInt(q.duplicates_merged)} />
        <Metric label="Missing days" value={`${fmtInt(q.gap_days)} of ${fmtInt(q.expected_days)}`} />
        <Metric label="Confirmed zero days" value={fmtInt(q.explicit_zero_rows)} />
      </div>
      <p className="text-xs text-faint">Score = {q.quality_formula}. Missing days are filled using the &ldquo;{ds.missing_policy}&rdquo; policy.</p>
      <div className="text-xs text-muted">
        Columns detected:{" "}
        {Object.entries(ds.column_mapping).map(([k, v]) => (
          <span key={k} className="mr-2">
            <Badge>{k} ← {v}</Badge>
          </span>
        ))}
      </div>
      {q.warnings?.map((w) => (
        <Notice key={w} tone="warn">
          {w}
        </Notice>
      ))}
      {q.error_samples && q.error_samples.length > 0 && (
        <div>
          <h3 className="mb-2 text-sm font-medium">Rows that were rejected (first {q.error_samples.length})</h3>
          <Table caption="Rejected rows">
            <thead>
              <tr>
                <Th>Row</Th>
                <Th>Problem</Th>
                <Th>Detail</Th>
              </tr>
            </thead>
            <tbody>
              {q.error_samples.map((e, i) => (
                <tr key={i}>
                  <Td className="tabular">{e.row}</Td>
                  <Td>
                    <Badge tone="bad">{e.code.replace(/_/g, " ")}</Badge>
                  </Td>
                  <Td className="text-muted">{e.message}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-line bg-bg/40 p-3">
      <div className="text-xs text-muted">{label}</div>
      <div className="tabular mt-1 text-sm font-medium">{value}</div>
    </div>
  );
}

const STEP_STATE_TEXT: Record<StepState, string> = {
  done: "completed",
  current: "current step",
  upcoming: "not started",
  unavailable: "not available for your role",
};

function WizardProgress({ steps }: { steps: WizardStep[] }) {
  return (
    <nav aria-label="Upload wizard" className="mb-6">
      <ol className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-0">
        {steps.map((s, i) => (
          <li key={s.key} aria-current={s.state === "current" ? "step" : undefined} className="flex items-center gap-2 sm:flex-1">
            <span
              className={clsx(
                "flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold ring-1 ring-inset",
                s.state === "done" && "bg-ok/15 text-ok ring-ok/40",
                s.state === "current" && "bg-primary/15 text-primary ring-primary/50",
                s.state === "upcoming" && "text-muted ring-line",
                s.state === "unavailable" && "text-warn ring-warn/40",
              )}
              aria-hidden
            >
              {s.state === "done" ? <Check className="h-4 w-4" /> : s.state === "unavailable" ? <Lock className="h-3.5 w-3.5" /> : i + 1}
            </span>
            <span className={clsx("text-sm", s.state === "current" ? "font-medium text-ink" : "text-muted")}>
              {s.label}
              <span className="sr-only"> ({STEP_STATE_TEXT[s.state]})</span>
              {s.state === "unavailable" && <span className="ml-1 text-xs text-warn" aria-hidden>· not for your role</span>}
            </span>
            {i < steps.length - 1 && <span className="mx-3 hidden h-px flex-1 bg-line sm:block" aria-hidden />}
          </li>
        ))}
      </ol>
    </nav>
  );
}

/** Step 3: start a training job and follow it. Shown only when the gate is ready; otherwise explains why not. */
function TrainStep({ gate, training, onStarted }: { gate: TrainGate; training: ReturnType<typeof useTrainingJob>; onStarted: (jobId: string) => void }) {
  const { job, refreshRecent } = training;
  const running = !!job.data && !TERMINAL.has(job.data.status);

  if (gate.status !== "ready") {
    return <Notice tone={gate.status === "forbidden" ? "warn" : "info"}>{gate.reason}</Notice>;
  }
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">
        Trains the Transformer on all of your organisation&apos;s sales history in the background and compares it with the statistical baselines.
      </p>
      <TrainForm onStarted={(j) => onStarted(j.id)} running={running} onError={() => void refreshRecent()} />
      {job.data && <JobProgress job={job.data} canCancel onChanged={() => job.mutate()} />}
      {job.data?.status === "succeeded" && (
        <Notice tone="ok">
          Training finished. Compare the new version with the baselines in{" "}
          <Link className="underline underline-offset-2" href="/models">
            Models &amp; drift
          </Link>
          .
        </Notice>
      )}
    </div>
  );
}

export default function UploadPage() {
  const { can, me } = useSession();
  const canTag = can("tag_datasets");
  const [offset, setOffset] = useState(0);
  const list = useSWR<Page<Dataset>>(`/datasets?limit=10&offset=${offset}`);
  const [file, setFile] = useState<File | null>(null);
  const [policy, setPolicy] = useState("zero");
  const [source, setSource] = useState("");
  const [fileError, setFileError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ dataset: Dataset; duplicate: boolean } | null>(null);
  const [selected, setSelected] = useState<Dataset | null>(null);
  const [reviewedId, setReviewedId] = useState<string | null>(null);
  const reviewRef = useRef<HTMLHeadingElement>(null);
  const trainRef = useRef<HTMLHeadingElement>(null);

  const [invFile, setInvFile] = useState<File | null>(null);
  const [invBusy, setInvBusy] = useState(false);
  const [invResult, setInvResult] = useState<string | null>(null);
  const [invError, setInvError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const fe = validateFile(file);
    setFileError(fe);
    if (fe || !file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    const form = new FormData();
    form.append("file", file);
    form.append("missing_policy", policy);
    // Metadata tags go through the Analyst+ programmatic endpoint; plain uploads use the Operator+ one.
    const tagged = canTag && source.trim().length > 0;
    if (tagged) form.append("tags", JSON.stringify({ source: source.trim().slice(0, 100) }));
    try {
      const r = await api<{ dataset: Dataset; duplicate: boolean }>(tagged ? "/datasets/upload" : "/upload-dataset", {
        method: "POST",
        form,
      });
      setResult(r);
      setSelected(r.dataset);
      reviewRef.current?.focus();
      await list.mutate();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function onInventory(e: FormEvent) {
    e.preventDefault();
    if (!invFile) return setInvError("Choose an inventory CSV.");
    setInvBusy(true);
    setInvError(null);
    setInvResult(null);
    const form = new FormData();
    form.append("file", invFile);
    try {
      const r = await api<{ updated: number; unknown_count: number; errors: { row: number; sku: string; message: string }[] }>("/inventory/upload", { method: "POST", form });
      setInvResult(`Updated ${r.updated} products. ${r.unknown_count} unknown SKUs skipped. ${r.errors.length} rows had errors${r.errors.length ? ` (e.g. row ${r.errors[0]!.row}: ${r.errors[0]!.message})` : ""}.`);
    } catch (err) {
      setInvError(errorMessage(err));
    } finally {
      setInvBusy(false);
    }
  }

  const canUpload = can("upload_data");
  const wizard = { canTrain: can("train_models"), role: me?.role, dataset: selected, reviewed: !!selected && reviewedId === selected.id };
  const gate = trainGate(wizard);
  const [trainJobId, setTrainJobId] = useState<string | null>(null);
  const training = useTrainingJob(trainJobId, gate.status === "ready");
  const steps = wizardSteps({ ...wizard, trainingFinished: training.job.data?.status === "succeeded" });

  return (
    <>
      <PageHeader title="Upload data" description="Upload historical sales, review the validation results, then train a model." />
      <WizardProgress steps={steps} />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,420px)_1fr]">
        <div className="space-y-6">
          <Card title="1. Upload sales history" subtitle="CSV, Excel or JSON with a date, a product/SKU and a quantity column.">
            {!canUpload ? (
              <Notice tone="info">Your role can view datasets but not upload them.</Notice>
            ) : (
              <form onSubmit={onSubmit} className="space-y-4" noValidate>
                <Field label="File" htmlFor="file" error={fileError} hint={`Up to ${MAX_MB} MB. Columns like date/order_date, sku/product_id, qty/units_sold are recognised.`}>
                  <Input id="file" type="file" accept={ACCEPT} onChange={(e) => { setFile(e.target.files?.[0] ?? null); setFileError(null); }} />
                </Field>
                <Field label="Dates missing from the file mean…" htmlFor="policy" hint="Choose 'no sales' for POS exports that omit zero-sale days.">
                  <Select id="policy" value={policy} onChange={(e) => setPolicy(e.target.value)}>
                    <option value="zero">No sales that day (fill with 0)</option>
                    <option value="interpolate">Unknown (interpolate between days)</option>
                  </Select>
                </Field>
                {canTag && (
                  <Field label="Source tag (optional)" htmlFor="source">
                    <Input id="source" value={source} maxLength={100} placeholder="e.g. pos-export-q2" onChange={(e) => setSource(e.target.value)} />
                  </Field>
                )}
                {error && <Notice tone="bad">{error}</Notice>}
                <Button type="submit" variant="primary" loading={busy}>
                  Upload and validate
                </Button>
              </form>
            )}
          </Card>
          <Card title="Inventory on hand" subtitle="Optional. CSV with sku and on_hand (optional: on_order, lead_time_days, unit_cost). Needed for reorder recommendations.">
            {!can("manage_inventory") ? (
              <Notice tone="info">Your role cannot change inventory.</Notice>
            ) : (
              <form onSubmit={onInventory} className="space-y-4" noValidate>
                <Field label="Inventory file" htmlFor="inv">
                  <Input id="inv" type="file" accept=".csv" onChange={(e) => setInvFile(e.target.files?.[0] ?? null)} />
                </Field>
                {invError && <Notice tone="bad">{invError}</Notice>}
                {invResult && <Notice tone="ok">{invResult}</Notice>}
                <Button type="submit" loading={invBusy}>
                  Upload inventory
                </Button>
              </form>
            )}
          </Card>
          <Card title="Next steps">
            <ul className="list-inside list-disc space-y-1 text-sm text-muted">
              <li>
                Review products and their model health in <Link className="text-primary hover:underline" href="/products">Products</Link>.
              </li>
              <li>
                Statistical forecasts work without training; see them in <Link className="text-primary hover:underline" href="/forecasts">Forecasts</Link>.
              </li>
              <li>
                See reorder suggestions in <Link className="text-primary hover:underline" href="/inventory">Recommendations</Link>.
              </li>
            </ul>
          </Card>
        </div>

        <div className="space-y-6">
          {result?.duplicate && <Notice tone="info">This exact file was uploaded before, so nothing was re-imported. Its original report is shown.</Notice>}
          <Card
            title={
              <span ref={reviewRef} tabIndex={-1} className="focus:outline-none">
                2. Review data quality{selected ? `: ${selected.filename}` : ""}
              </span>
            }
            subtitle={selected ? fmtDateTime(selected.created_at) : undefined}
          >
            {!selected ? (
              <EmptyState title="Nothing to review yet">Upload a file in step 1, or open a report from the upload history.</EmptyState>
            ) : (
              <div className="space-y-4">
                <QualityReport ds={selected} />
                {selected.status === "imported" &&
                  (reviewedId === selected.id ? (
                    <Notice tone="ok">Quality reviewed. Continue with step 3.</Notice>
                  ) : (
                    <Button
                      variant="primary"
                      onClick={() => {
                        setReviewedId(selected.id);
                        // Wait for step 3 to render its form before moving focus there.
                        requestAnimationFrame(() => trainRef.current?.focus());
                      }}
                    >
                      Continue to training
                    </Button>
                  ))}
              </div>
            )}
          </Card>
          <Card
            title={
              <span ref={trainRef} tabIndex={-1} className="focus:outline-none">
                3. Train a forecasting model
              </span>
            }
            subtitle="Optional. A new version is used only if it passes the quality gate against the statistical baselines."
          >
            <TrainStep gate={gate} training={training} onStarted={setTrainJobId} />
          </Card>
          <Card title="Upload history">
            {list.error ? (
              <ErrorState error={list.error} onRetry={() => list.mutate()} />
            ) : !list.data ? (
              <LoadingBlock rows={4} />
            ) : list.data.items.length === 0 ? (
              <EmptyState title="No datasets uploaded yet" />
            ) : (
              <>
                <Table caption="Uploaded datasets">
                  <thead>
                    <tr>
                      <Th>File</Th>
                      <Th>Uploaded</Th>
                      <Th>Status</Th>
                      <Th className="text-right">Rows</Th>
                      <Th className="text-right">Quality</Th>
                      <Th />
                    </tr>
                  </thead>
                  <tbody>
                    {list.data.items.map((d) => (
                      <tr key={d.id}>
                        <Td>
                          <div className="font-medium">{d.filename}</div>
                          <div className="text-xs text-faint">{fmtBytes(d.size_bytes)} · {d.file_format}</div>
                        </Td>
                        <Td className="text-muted">{fmtDateTime(d.created_at)}</Td>
                        <Td>
                          <Badge tone={d.status === "imported" ? "ok" : "bad"}>{d.status}</Badge>
                        </Td>
                        <Td className="tabular text-right">{fmtInt(d.accepted_rows)}</Td>
                        <Td className="tabular text-right">{d.quality_score.toFixed(1)}</Td>
                        <Td className="text-right">
                          <Button
                            variant="ghost"
                            onClick={() => {
                              setSelected(d);
                              reviewRef.current?.focus();
                            }}
                          >
                            Report<span className="sr-only"> for {d.filename}</span>
                          </Button>
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
                <Pagination total={list.data.total} limit={10} offset={offset} onChange={setOffset} />
              </>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
