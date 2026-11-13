"use client";

import { useState, type FormEvent } from "react";
import useSWR from "swr";

import { Badge, Button, Card, ConfirmButton, ErrorState, Field, Input, LoadingBlock, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { describeOverride, overrideErrors, type OverrideForm } from "@/lib/purchasing";
import type { ForecastOverride, Page } from "@/lib/types";

const EMPTY: OverrideForm = { start_date: "", end_date: "", kind: "scale", value: "1.2", reason: "" };

/** A product's planner overrides: list, add, archive. Overrides change the demand used for reorder decisions; the
 *  model's own forecast is kept, so each override can be scored later. */
export function OverridesCard({ productId, editable, onChanged }: { productId: string; editable: boolean; onChanged?: () => void | Promise<void> }) {
  const list = useSWR<Page<ForecastOverride>>(`/forecast-overrides?product_id=${productId}`);
  const [f, setF] = useState<OverrideForm>(EMPTY);
  const [errors, setErrors] = useState<Partial<Record<keyof OverrideForm, string>>>({});
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    const v = overrideErrors(f);
    setErrors(v);
    if (Object.keys(v).length) return;
    setBusy(true);
    try {
      await api("/forecast-overrides", { method: "POST", body: { product_id: productId, ...f, value: Number(f.value), reason: f.reason.trim() } });
      setF(EMPTY);
      await list.mutate();
      await onChanged?.();
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Planner overrides" subtitle="Adjust the demand used for reorder decisions over a date range. The model forecast is kept, so each override is scored against actual sales.">
      {list.error ? (
        <ErrorState error={list.error} onRetry={() => list.mutate()} />
      ) : !list.data ? (
        <LoadingBlock rows={2} />
      ) : list.data.items.length === 0 ? (
        <p className="text-sm text-muted">No active overrides.</p>
      ) : (
        <ul className="space-y-2 text-sm">
          {list.data.items.map((o) => (
            <li key={o.id} className="flex flex-wrap items-center gap-2">
              <Badge tone="ai">{describeOverride(o.kind, o.value)}</Badge>
              <span>{fmtDate(o.start_date)} – {fmtDate(o.end_date)}</span>
              <span className="text-muted">{o.reason}</span>
              {editable && (
                <ConfirmButton
                  title="Stop applying this override?"
                  body="It is archived, not deleted, so its accuracy can still be reviewed."
                  confirmLabel="Archive"
                  onConfirm={async () => {
                    await api(`/forecast-overrides/${o.id}/archive`, { method: "POST" });
                    await list.mutate();
                    await onChanged?.();
                  }}
                >
                  Archive
                </ConfirmButton>
              )}
            </li>
          ))}
        </ul>
      )}
      {editable && (
        <form onSubmit={submit} className="mt-4 grid gap-3 sm:grid-cols-2" aria-label="Add an override">
          <Field label="From" htmlFor="ov-s" error={errors.start_date}><Input id="ov-s" type="date" value={f.start_date} onChange={(e) => setF({ ...f, start_date: e.target.value })} /></Field>
          <Field label="To" htmlFor="ov-e" error={errors.end_date}><Input id="ov-e" type="date" value={f.end_date} onChange={(e) => setF({ ...f, end_date: e.target.value })} /></Field>
          <Field label="Type" htmlFor="ov-k">
            <Select id="ov-k" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as OverrideForm["kind"] })}>
              <option value="scale">Scale the forecast</option>
              <option value="set">Set units per day</option>
            </Select>
          </Field>
          <Field label={f.kind === "scale" ? "Factor (1.2 = +20%)" : "Units per day"} htmlFor="ov-v" error={errors.value}>
            <Input id="ov-v" inputMode="decimal" value={f.value} onChange={(e) => setF({ ...f, value: e.target.value })} />
          </Field>
          <div className="sm:col-span-2">
            <Field label="Reason" htmlFor="ov-r" error={errors.reason} hint="For example: promotion booked, new listing, supplier change">
              <Input id="ov-r" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} maxLength={500} />
            </Field>
          </div>
          <div className="sm:col-span-2"><Button type="submit" variant="primary" loading={busy}>Add override</Button></div>
          {err != null && <div className="sm:col-span-2"><ErrorState error={err} /></div>}
        </form>
      )}
    </Card>
  );
}
