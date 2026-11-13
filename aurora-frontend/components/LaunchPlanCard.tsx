"use client";

import { useState, type FormEvent } from "react";
import useSWR from "swr";

import { TrendChart } from "@/components/charts";
import { ProductPicker } from "@/components/ProductPicker";
import { Badge, Button, Card, ConfirmButton, ErrorState, Field, Input, LoadingBlock, Notice } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDate, fmtInt } from "@/lib/format";
import { MAX_ANALOGUES, launchBody, launchStage, type LaunchForm } from "@/lib/launches";
import type { LaunchPreview, LaunchSuggestion } from "@/lib/types";

const today = () => new Date().toISOString().slice(0, 10);

function fromPreview(p: LaunchPreview | null): LaunchForm {
  if (!p) return { launch_date: "", analogue_ids: [""], scale_pct: "100", notes: "" };
  return { launch_date: p.plan.launch_date, analogue_ids: p.plan.analogue_ids, scale_pct: String(Math.round(p.plan.scale * 100)), notes: p.plan.notes };
}

/** Plan a new product's demand from similar products until it has sales of its own. */
export function LaunchPlanCard({ productId, editable, onChanged }: { productId: string; editable: boolean; onChanged?: () => void | Promise<void> }) {
  const data = useSWR<LaunchPreview | { plan: null }>(`/products/${productId}/launch`);
  const suggestions = useSWR<LaunchSuggestion[]>(editable ? `/products/${productId}/launch/analogues` : null);
  const [form, setForm] = useState<LaunchForm | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const preview = data.data && data.data.plan ? (data.data as LaunchPreview) : null;
  const editing = form !== null;

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!form) return;
    const built = launchBody(form);
    if ("error" in built) return setErr(new Error(built.error));
    setErr(null);
    setBusy(true);
    try {
      const out = await api<LaunchPreview>(`/products/${productId}/launch`, {
        method: "PUT",
        body: { ...built.body, expected_version: preview ? preview.plan.version : 0 },
      });
      await data.mutate(out, { revalidate: false });
      setForm(null);
      await onChanged?.();
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    await api(`/products/${productId}/launch`, { method: "DELETE" });
    await data.mutate({ plan: null }, { revalidate: false });
    await onChanged?.();
  };

  const setAnalogue = (i: number, v: string) => form && setForm({ ...form, analogue_ids: form.analogue_ids.map((x, j) => (j === i ? v : x)) });

  return (
    <Card
      title="Launch plan"
      subtitle="For a new product: base its demand on similar products' first weeks until it has about 8 weeks of sales of its own."
      actions={
        editable && !editing ? (
          <Button variant="secondary" onClick={() => setForm(fromPreview(preview))}>{preview ? "Edit plan" : "Plan a launch"}</Button>
        ) : undefined
      }
    >
      {data.error ? (
        <ErrorState error={data.error} onRetry={() => data.mutate()} />
      ) : !data.data ? (
        <LoadingBlock rows={2} label="Loading launch plan" />
      ) : editing ? (
        <form onSubmit={save} className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Launch date" htmlFor="ln-date">
              <Input id="ln-date" type="date" value={form.launch_date} onChange={(e) => setForm({ ...form, launch_date: e.target.value })} />
            </Field>
            <Field label="Expected sales (% of similar products)" htmlFor="ln-scale" hint="100% = sells like them on average">
              <Input id="ln-scale" type="number" min={5} max={2000} step={5} value={form.scale_pct} onChange={(e) => setForm({ ...form, scale_pct: e.target.value })} />
            </Field>
          </div>
          <fieldset className="space-y-2">
            <legend className="text-sm text-muted">Similar products (they need 4+ weeks of sales)</legend>
            {form.analogue_ids.map((v, i) => (
              <div key={i} className="flex gap-2">
                <label className="sr-only" htmlFor={`ln-a${i}`}>Similar product {i + 1}</label>
                <ProductPicker id={`ln-a${i}`} value={v} onChange={(id) => setAnalogue(i, id)} />
                {form.analogue_ids.length > 1 && (
                  <Button variant="ghost" onClick={() => setForm({ ...form, analogue_ids: form.analogue_ids.filter((_, j) => j !== i) })}>Remove</Button>
                )}
              </div>
            ))}
            {form.analogue_ids.length < MAX_ANALOGUES && (
              <Button variant="ghost" onClick={() => setForm({ ...form, analogue_ids: [...form.analogue_ids, ""] })}>Add another</Button>
            )}
            {suggestions.data && suggestions.data.length > 0 && (
              <p className="text-xs text-muted">
                Suggested from the same category:{" "}
                {suggestions.data.map((s, i) => (
                  <span key={s.product_id}>
                    {i > 0 && ", "}
                    <button
                      type="button"
                      className="text-primary hover:underline"
                      onClick={() => {
                        const ids = form.analogue_ids.filter(Boolean);
                        if (!ids.includes(s.product_id) && ids.length < MAX_ANALOGUES) setForm({ ...form, analogue_ids: [...ids, s.product_id] });
                      }}
                    >
                      {s.sku}
                    </button>
                  </span>
                ))}
              </p>
            )}
          </fieldset>
          <Field label="Notes" htmlFor="ln-notes">
            <Input id="ln-notes" maxLength={500} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          </Field>
          <div className="flex gap-2">
            <Button type="submit" loading={busy}>Save launch plan</Button>
            <Button variant="ghost" onClick={() => { setForm(null); setErr(null); }}>Cancel</Button>
          </div>
          {err != null && <ErrorState error={err} />}
        </form>
      ) : !preview ? (
        <p className="text-sm text-muted">No launch plan. {editable ? "Add one if this product is new and has little or no sales history." : ""}</p>
      ) : (
        <div className="space-y-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={preview.active ? "info" : "default"}>{preview.active ? "in use" : "finished"}</Badge>
            <span className="text-muted">
              Launch {fmtDate(preview.plan.launch_date)} · {Math.round(preview.plan.scale * 100)}% of{" "}
              {preview.forecast.launch.analogues.filter((a) => a.used).map((a) => a.sku).join(", ")}
            </span>
          </div>
          <p className="text-muted">{launchStage(preview.forecast.launch.own_days, preview.handover_days, preview.plan.launch_date, today())}.</p>
          <div className="grid grid-cols-2 gap-3">
            <div><div className="text-xs text-muted">{preview.forecast.launch.own_days ? "Rest of first 4 weeks" : "First 4 weeks"}</div><div className="text-lg text-ink tabular-nums">{fmtInt(preview.totals.first_4_weeks)} units</div></div>
            <div><div className="text-xs text-muted">{preview.forecast.launch.own_days ? "Rest of first 13 weeks" : "First 13 weeks"}</div><div className="text-lg text-ink tabular-nums">{fmtInt(preview.totals.first_13_weeks)} units</div></div>
          </div>
          <TrendChart label="Expected daily units after launch" points={preview.forecast.points.map((p) => ({ date: p.date, units: p.yhat }))} />
          {preview.forecast.launch.analogues.filter((a) => !a.used).map((a) => (
            <Notice key={a.product_id} tone="warn">{a.sku} is not used: {a.reason}.</Notice>
          ))}
          {preview.plan.notes && <p className="text-muted">{preview.plan.notes}</p>}
          {editable && (
            <ConfirmButton
              title="Delete this launch plan?"
              body="Recommendations for this product go back to its own sales history (none, for a product that has not sold)."
              confirmLabel="Delete plan"
              onConfirm={remove}
            >
              Delete plan
            </ConfirmButton>
          )}
        </div>
      )}
    </Card>
  );
}
