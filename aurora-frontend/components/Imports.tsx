"use client";

import { useState, type FormEvent } from "react";
import useSWR, { useSWRConfig } from "swr";

import { Badge, Button, Card, ConfirmButton, EmptyState, ErrorState, Field, Input, LoadingBlock, Notice, Select, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDateTime, fmtInt } from "@/lib/format";
import {
  EMPTY_FORM,
  KIND_LABEL,
  STATUS_TONE,
  importBody,
  importFormErrors,
  scheduleLabel,
  type ImportForm,
  type ImportRun,
  type ImportSource,
} from "@/lib/imports";

const HOURS = Array.from({ length: 24 }, (_, h) => h);

export function NewImportCard() {
  const { mutate } = useSWRConfig();
  const [f, setF] = useState<ImportForm>(EMPTY_FORM);
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [done, setDone] = useState<string | null>(null);
  const errors = touched ? importFormErrors(f) : {};
  const set = <K extends keyof ImportForm>(k: K, v: ImportForm[K]) => setF((x) => ({ ...x, [k]: v }));
  const woo = f.kind === "woocommerce";

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (Object.keys(importFormErrors(f)).length) return;
    setBusy(true);
    setErr(null);
    try {
      const s = await api<ImportSource>("/imports", { method: "POST", body: importBody(f) });
      setDone(`Saved “${s.name}”. Use Import now to test it.`);
      setF(EMPTY_FORM);
      setTouched(false);
      await mutate("/imports");
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="New scheduled import" subtitle="Sales come in on a schedule instead of by hand. Credentials are stored encrypted and never shown again.">
      <form onSubmit={submit} className="grid gap-3 md:grid-cols-2" noValidate>
        <Field label="Import name" htmlFor="imp-name" error={errors.name}>
          <Input id="imp-name" value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="Nightly POS export" maxLength={200} />
        </Field>
        <Field label="Source type" htmlFor="imp-kind">
          <Select id="imp-kind" value={f.kind} onChange={(e) => set("kind", e.target.value as ImportForm["kind"])}>
            {Object.entries(KIND_LABEL).map(([k, label]) => (
              <option key={k} value={k}>{label}</option>
            ))}
          </Select>
        </Field>
        <div className="md:col-span-2">
          <Field
            label={woo ? "Store address" : "File address"}
            htmlFor="imp-url"
            error={errors.url}
            hint={woo ? "The shop's home page, for example https://shop.example.com" : "A CSV, Excel or JSON file in the same layout as a manual upload. Links with a token in them are fine: the address is stored encrypted."}
          >
            <Input id="imp-url" type="url" inputMode="url" value={f.url} onChange={(e) => set("url", e.target.value)} placeholder={woo ? "https://shop.example.com" : "https://exports.example.com/sales.csv"} />
          </Field>
        </div>
        {woo ? (
          <>
            <Field label="Consumer key" htmlFor="imp-ck" error={errors.consumerKey} hint="WooCommerce → Settings → Advanced → REST API; read access is enough">
              <Input id="imp-ck" autoComplete="off" value={f.consumerKey} onChange={(e) => set("consumerKey", e.target.value)} />
            </Field>
            <Field label="Consumer secret" htmlFor="imp-cs" error={errors.consumerSecret}>
              <Input id="imp-cs" type="password" autoComplete="new-password" value={f.consumerSecret} onChange={(e) => set("consumerSecret", e.target.value)} />
            </Field>
            <Field label="Look-back window (days)" htmlFor="imp-lb" error={errors.lookbackDays} hint="Each import re-reads this many days of orders, so late changes and cancellations are picked up">
              <Input id="imp-lb" type="number" min={1} max={90} value={f.lookbackDays} onChange={(e) => set("lookbackDays", e.target.value)} />
            </Field>
          </>
        ) : (
          <>
            <Field label="Authorization header (optional)" htmlFor="imp-auth" error={errors.authorization} hint="Sent as is, for example Bearer abc123">
              <Input id="imp-auth" type="password" autoComplete="new-password" value={f.authorization} onChange={(e) => set("authorization", e.target.value)} />
            </Field>
            <Field label="Days missing from the file" htmlFor="imp-missing">
              <Select id="imp-missing" value={f.missingPolicy} onChange={(e) => set("missingPolicy", e.target.value as ImportForm["missingPolicy"])}>
                <option value="zero">Count as zero sales</option>
                <option value="interpolate">Fill in from neighbouring days</option>
              </Select>
            </Field>
          </>
        )}
        <Field label="How often" htmlFor="imp-freq">
          <Select id="imp-freq" value={f.frequency} onChange={(e) => set("frequency", e.target.value as ImportForm["frequency"])}>
            <option value="daily">Daily</option>
            <option value="hourly">Every hour</option>
          </Select>
        </Field>
        {f.frequency === "daily" && (
          <Field label="Time (UTC)" htmlFor="imp-hour">
            <Select id="imp-hour" value={f.hourUtc} onChange={(e) => set("hourUtc", e.target.value)}>
              {HOURS.map((h) => (
                <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>
              ))}
            </Select>
          </Field>
        )}
        <div className="flex flex-wrap items-center gap-3 md:col-span-2">
          <Button type="submit" variant="primary" loading={busy}>Save import</Button>
          {done && <span role="status" className="text-sm text-ok">{done}</span>}
        </div>
        {err != null && <div className="md:col-span-2"><ErrorState error={err} /></div>}
      </form>
    </Card>
  );
}

export function ImportSourcesCard() {
  const list = useSWR<ImportSource[]>("/imports");
  return (
    <Card title="Scheduled imports" subtitle="Each one runs on its schedule; Import now runs it at once and is the way to test a new one.">
      {list.error ? (
        <ErrorState error={list.error} onRetry={() => void list.mutate()} />
      ) : !list.data ? (
        <LoadingBlock />
      ) : list.data.length === 0 ? (
        <EmptyState title="No scheduled imports yet">Add one below to bring in sales from a file address or a WooCommerce store.</EmptyState>
      ) : (
        <ul className="space-y-4">
          {list.data.map((s) => (
            <SourceItem key={s.id} source={s} />
          ))}
        </ul>
      )}
    </Card>
  );
}

function SourceItem({ source: s }: { source: ImportSource }) {
  const { mutate } = useSWRConfig();
  const [busy, setBusy] = useState<"run" | "toggle" | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [history, setHistory] = useState(false);
  const [removeSales, setRemoveSales] = useState(false);
  const refresh = async () => {
    await mutate("/imports");
    await mutate(`/imports/${s.id}/runs`);
  };

  const act = async (kind: "run" | "toggle") => {
    setBusy(kind);
    setErr(null);
    try {
      if (kind === "run") await api(`/imports/${s.id}/run`, { method: "POST" });
      else await api(`/imports/${s.id}`, { method: "PATCH", body: { expected_version: s.version, is_active: !s.is_active } });
      await refresh();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  };

  return (
    <li className="rounded-lg border border-line p-4" aria-label={`Import ${s.name}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-ink">{s.name}</span>
            <Badge tone="info">{KIND_LABEL[s.kind]}</Badge>
            {!s.is_active && <Badge tone="warn">Paused</Badge>}
          </div>
          <div className="mt-1 break-all text-xs text-muted">
            {s.url_hint} · {scheduleLabel(s)}
            {s.kind === "woocommerce" && s.options.lookback_days ? ` · last ${s.options.lookback_days} days re-read` : ""}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => void act("run")} loading={busy === "run"}>Import now</Button>
          <Button variant="ghost" onClick={() => void act("toggle")} loading={busy === "toggle"}>{s.is_active ? "Pause" : "Resume"}</Button>
          <Button variant="ghost" onClick={() => setHistory((h) => !h)} aria-expanded={history}>
            {history ? "Hide history" : "History"}
          </Button>
          <ConfirmButton
            title={`Delete “${s.name}”?`}
            confirmLabel="Delete import"
            onConfirm={async () => {
              await api(`/imports/${s.id}?remove_sales=${removeSales}`, { method: "DELETE" });
              await mutate("/imports");
            }}
            body={
              <div className="space-y-3">
                <p>The schedule and its history go. Sales it already imported stay unless you tick the box.</p>
                <label className="flex items-start gap-2 text-ink">
                  <input type="checkbox" checked={removeSales} onChange={(e) => setRemoveSales(e.target.checked)} className="mt-1" />
                  Also delete the sales this import brought in
                </label>
              </div>
            }
          >
            Delete
          </ConfirmButton>
        </div>
      </div>
      <div className="mt-3 text-sm">
        {s.last_status ? (
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={STATUS_TONE[s.last_status]}>{s.last_status}</Badge>
            <span className="text-muted">{fmtDateTime(s.last_run_at)}</span>
            <span className="text-ink">{s.last_message}</span>
          </div>
        ) : (
          <span className="text-muted">Not run yet. Next run {fmtDateTime(s.next_run_at)}.</span>
        )}
        {!s.is_active && s.consecutive_failures > 0 && (
          <div className="mt-2">
            <Notice tone="warn">Paused after {s.consecutive_failures} failed attempts in a row. Fix the problem, then resume it.</Notice>
          </div>
        )}
      </div>
      {err != null && <div className="mt-3"><ErrorState error={err} /></div>}
      {history && <RunHistory sourceId={s.id} name={s.name} />}
    </li>
  );
}

function RunHistory({ sourceId, name }: { sourceId: string; name: string }) {
  const runs = useSWR<ImportRun[]>(`/imports/${sourceId}/runs`);
  if (runs.error) return <ErrorState error={runs.error} />;
  if (!runs.data) return <LoadingBlock rows={2} />;
  if (!runs.data.length) return <p className="mt-3 text-sm text-muted">No runs yet.</p>;
  return (
    <div className="mt-3">
      <Table caption={`Import history for ${name}`}>
        <thead>
          <tr>
            <Th>When</Th>
            <Th>Started by</Th>
            <Th>Result</Th>
            <Th className="text-right">Rows</Th>
            <Th>Details</Th>
          </tr>
        </thead>
        <tbody>
          {runs.data.map((r) => (
            <tr key={r.id} className="border-t border-line">
              <Td>{fmtDateTime(r.started_at)}</Td>
              <Td>{r.trigger === "manual" ? "Import now" : "Schedule"}</Td>
              <Td><Badge tone={STATUS_TONE[r.status]}>{r.status}</Badge></Td>
              <Td className="text-right tabular-nums">{fmtInt(r.rows)}</Td>
              <Td className="text-muted">{r.message}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
    </div>
  );
}
