"use client";

import { useState, type FormEvent } from "react";

import { Button, ErrorState, Field, Input, Notice, Select } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { Product } from "@/lib/types";

type FormState = {
  on_hand: string;
  on_order: string;
  lead_time_days: string;
  service_level: string;
  unit_cost: string;
  pack_size: string;
  min_order_qty: string;
  missing_policy: "zero" | "interpolate";
};

const str = (v: number | null | undefined) => (v == null ? "" : String(v));

function initial(p: Product): FormState {
  return {
    on_hand: p.inventory_known ? String(p.on_hand) : "",
    on_order: String(p.on_order),
    lead_time_days: str(p.lead_time_days),
    service_level: p.service_level == null ? "" : String(Math.round(p.service_level * 1000) / 10),
    unit_cost: str(p.unit_cost),
    pack_size: String(p.pack_size),
    min_order_qty: String(p.min_order_qty),
    missing_policy: p.missing_policy,
  };
}

/** Validates the editor form and builds the PATCH body. Exported for unit tests. */
export function buildInventoryPatch(f: FormState, p: Product, opts: { omitStock?: boolean } = {}): { body?: Record<string, unknown>; error?: string } {
  const body: Record<string, unknown> = { expected_version: p.version };
  const int = (name: string, v: string, min: number, max: number) => {
    if (v.trim() === "") return undefined;
    const n = Number(v);
    if (!Number.isInteger(n) || n < min || n > max) throw new Error(`${name} must be a whole number between ${min} and ${max}`);
    return n;
  };
  try {
    // With several locations, stock is counted per location (StockByLocation), never as a product total.
    if (!opts.omitStock) {
      const onHand = int("On hand", f.on_hand, 0, 10_000_000);
      if (onHand !== undefined) body.on_hand = onHand;
      const onOrder = int("On order", f.on_order, 0, 10_000_000);
      if (onOrder !== undefined) body.on_order = onOrder;
    }
    const lt = int("Lead time", f.lead_time_days, 1, 365);
    if (lt !== undefined) body.lead_time_days = lt;
    if (f.service_level.trim() !== "") {
      const sl = Number(f.service_level);
      if (!Number.isFinite(sl) || sl <= 50 || sl >= 100) throw new Error("Service level must be between 50 and 100 percent (exclusive)");
      body.service_level = sl / 100;
    }
    if (f.unit_cost.trim() !== "") {
      const c = Number(f.unit_cost);
      if (!Number.isFinite(c) || c < 0) throw new Error("Unit cost must be zero or more");
      body.unit_cost = c;
    }
    const pack = int("Pack size", f.pack_size, 1, 100_000);
    if (pack !== undefined) body.pack_size = pack;
    const moq = int("Minimum order", f.min_order_qty, 0, 10_000_000);
    if (moq !== undefined) body.min_order_qty = moq;
    body.missing_policy = f.missing_policy;
  } catch (e) {
    return { error: (e as Error).message };
  }
  return { body };
}

export function InventoryEditor({
  product,
  readOnly,
  onSaved,
  stockByLocation = false,
}: {
  product: Product;
  readOnly: boolean;
  onSaved: (p: Product) => Promise<void> | void;
  /** True when the organisation has several locations: counts are then entered per location, not here. */
  stockByLocation?: boolean;
}) {
  const [f, setF] = useState<FormState>(() => initial(product));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const set = (k: keyof FormState) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value } as FormState);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    setMsg(null);
    const { body, error } = buildInventoryPatch(f, product, { omitStock: stockByLocation });
    if (error) {
      setErr(new Error(error));
      return;
    }
    setBusy(true);
    try {
      const updated = await api<Product>(`/products/${product.id}/inventory`, { method: "PATCH", body });
      setF(initial(updated));
      setMsg("Saved.");
      await onSaved(updated);
    } catch (e2) {
      if (e2 instanceof ApiError && e2.status === 409) {
        setErr(new Error("Someone else changed this product since you opened it. Reload the page and try again."));
      } else setErr(e2);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="grid grid-cols-2 gap-3">
      {stockByLocation ? (
        <p className="col-span-2 text-xs text-muted">Stock is counted per location, in the table below. The totals are the sum.</p>
      ) : (
        <>
          <Field label="On hand (units)" htmlFor="ie-oh" hint={product.inventory_known ? undefined : "Unknown: enter a count to get recommendations"}>
            <Input id="ie-oh" inputMode="numeric" value={f.on_hand} onChange={set("on_hand")} disabled={readOnly} />
          </Field>
          <Field label="On order (units)" htmlFor="ie-oo">
            <Input id="ie-oo" inputMode="numeric" value={f.on_order} onChange={set("on_order")} disabled={readOnly} />
          </Field>
        </>
      )}
      <Field label="Lead time (days)" htmlFor="ie-lt" hint="Blank = organisation default">
        <Input id="ie-lt" inputMode="numeric" value={f.lead_time_days} onChange={set("lead_time_days")} disabled={readOnly} />
      </Field>
      <Field label="Service level (%)" htmlFor="ie-sl" hint="Blank = default">
        <Input id="ie-sl" inputMode="decimal" value={f.service_level} onChange={set("service_level")} disabled={readOnly} />
      </Field>
      <Field label="Unit cost" htmlFor="ie-uc">
        <Input id="ie-uc" inputMode="decimal" value={f.unit_cost} onChange={set("unit_cost")} disabled={readOnly} />
      </Field>
      <Field label="Pack size" htmlFor="ie-ps">
        <Input id="ie-ps" inputMode="numeric" value={f.pack_size} onChange={set("pack_size")} disabled={readOnly} />
      </Field>
      <Field label="Minimum order qty" htmlFor="ie-moq">
        <Input id="ie-moq" inputMode="numeric" value={f.min_order_qty} onChange={set("min_order_qty")} disabled={readOnly} />
      </Field>
      <Field label="Missing days mean" htmlFor="ie-mp">
        <Select id="ie-mp" value={f.missing_policy} onChange={set("missing_policy")} disabled={readOnly}>
          <option value="zero">Zero sales</option>
          <option value="interpolate">Unknown (interpolate)</option>
        </Select>
      </Field>
      {!readOnly && (
        <div className="col-span-2 flex items-center gap-3">
          <Button type="submit" variant="primary" loading={busy}>Save</Button>
          {msg && <span className="text-sm text-ok" role="status">{msg}</span>}
        </div>
      )}
      {err != null && <div className="col-span-2"><ErrorState error={err} /></div>}
      {readOnly && <div className="col-span-2"><Notice>Your role can view but not edit stock settings.</Notice></div>}
    </form>
  );
}
