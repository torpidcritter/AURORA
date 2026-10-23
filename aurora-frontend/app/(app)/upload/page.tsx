"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import useSWR from "swr";

import { Badge, Button, Card, EmptyState, ErrorState, Field, Input, LoadingBlock, Notice, PageHeader, Pagination, Select, Table, Td, Th } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { fmtBytes, fmtDateTime, fmtInt } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Dataset, Page } from "@/lib/types";
import { MAX_UPLOAD_MB as MAX_MB, validateFile } from "@/lib/validation";

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

export default function UploadPage() {
  const { can } = useSession();
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
    if (source.trim()) form.append("tags", JSON.stringify({ source: source.trim().slice(0, 100) }));
    try {
      const r = await api<{ dataset: Dataset; duplicate: boolean }>("/datasets/upload", { method: "POST", form });
      setResult(r);
      setSelected(r.dataset);
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

  return (
    <>
      <PageHeader title="Upload data" description="Upload historical sales, review validation results, then train or forecast." />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,420px)_1fr]">
        <div className="space-y-6">
          <Card title="1. Sales history" subtitle="CSV, Excel or JSON with a date, a product/SKU and a quantity column.">
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
                <Field label="Source tag (optional)" htmlFor="source">
                  <Input id="source" value={source} maxLength={100} placeholder="e.g. pos-export-q2" onChange={(e) => setSource(e.target.value)} />
                </Field>
                {error && <Notice tone="bad">{error}</Notice>}
                <Button type="submit" variant="primary" loading={busy}>
                  Upload and validate
                </Button>
              </form>
            )}
          </Card>
          <Card title="2. Inventory on hand" subtitle="CSV with sku and on_hand (optional: on_order, lead_time_days, unit_cost). Needed for reorder recommendations.">
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
          <Card title="3. Next steps">
            <ul className="list-inside list-disc space-y-1 text-sm text-muted">
              <li>
                Review products in <Link className="text-primary hover:underline" href="/products">Products</Link>.
              </li>
              <li>
                Train the Transformer in <Link className="text-primary hover:underline" href="/models">Models</Link> (analysts). Statistical forecasts work without training.
              </li>
              <li>
                See reorder suggestions in <Link className="text-primary hover:underline" href="/inventory">Recommendations</Link>.
              </li>
            </ul>
          </Card>
        </div>

        <div className="space-y-6">
          {result?.duplicate && <Notice tone="info">This exact file was uploaded before, so nothing was re-imported. Its original report is shown.</Notice>}
          {selected && (
            <Card title={`Quality report: ${selected.filename}`} subtitle={fmtDateTime(selected.created_at)}>
              <QualityReport ds={selected} />
            </Card>
          )}
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
                          <Button variant="ghost" onClick={() => setSelected(d)}>
                            Report
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
