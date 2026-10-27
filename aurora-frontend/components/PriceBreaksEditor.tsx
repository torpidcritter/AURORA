"use client";

import { useState } from "react";

import { Button, ErrorState, Input, Notice } from "@/components/ui";
import { api } from "@/lib/api";
import { fmt2, fmtInt } from "@/lib/format";
import { type BreakRow, MAX_BREAKS, parseBreaks } from "@/lib/pricing";
import type { PriceBreak, Product } from "@/lib/types";

const toRows = (breaks: PriceBreak[] | null | undefined): BreakRow[] =>
  (breaks ?? []).map((b) => ({ min_qty: String(b.min_qty), unit_cost: String(b.unit_cost) }));

/** Quantity discounts for a product. Purchase orders and recommendations price orders with them. */
export function PriceBreaksEditor({ product, editable, onSaved }: { product: Product; editable: boolean; onSaved: () => void | Promise<void> }) {
  const [rows, setRows] = useState<BreakRow[]>(() => toRows(product.price_breaks));
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const parsed = parseBreaks(rows, product.unit_cost);
  const breaks = product.price_breaks ?? [];

  if (!editable) {
    return breaks.length ? (
      <ul className="text-sm text-muted">
        {breaks.map((b) => <li key={b.min_qty}>From {fmtInt(b.min_qty)} units: {fmt2(b.unit_cost)} each</li>)}
      </ul>
    ) : (
      <p className="text-sm text-muted">No price breaks.</p>
    );
  }

  const save = async () => {
    setErr(null);
    setBusy(true);
    try {
      await api(`/products/${product.id}/price-breaks`, {
        method: "PUT",
        body: { expected_version: product.version, breaks: parsed.breaks },
      });
      await onSaved();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const set = (i: number, key: keyof BreakRow, v: string) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, [key]: v } : r)));

  return (
    <div>
      <p className="mb-2 text-xs text-faint">
        Base price {product.unit_cost == null ? "not set" : fmt2(product.unit_cost)}. Each break applies from its minimum quantity and must be cheaper than the tier below.
      </p>
      {rows.length > 0 && (
        <table className="mb-2 text-sm">
          <caption className="sr-only">Price breaks</caption>
          <thead>
            <tr className="text-left text-xs text-muted">
              <th className="pr-2 font-normal">From units</th>
              <th className="pr-2 font-normal">Unit cost</th>
              <th><span className="sr-only">Remove</span></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td className="pr-2 pb-1">
                  <Input aria-label={`Break ${i + 1} from units`} inputMode="numeric" value={r.min_qty} onChange={(e) => set(i, "min_qty", e.target.value)} className="w-24" />
                </td>
                <td className="pr-2 pb-1">
                  <Input aria-label={`Break ${i + 1} unit cost`} inputMode="decimal" value={r.unit_cost} onChange={(e) => set(i, "unit_cost", e.target.value)} className="w-24" />
                </td>
                <td className="pb-1">
                  <Button variant="ghost" onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}>Remove</Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {parsed.error && <Notice tone="warn">{parsed.error}</Notice>}
      <div className="mt-2 flex flex-wrap gap-2">
        <Button variant="secondary" disabled={rows.length >= MAX_BREAKS} onClick={() => setRows((rs) => [...rs, { min_qty: "", unit_cost: "" }])}>
          Add price break
        </Button>
        <Button onClick={() => void save()} loading={busy} disabled={parsed.error != null}>Save price breaks</Button>
      </div>
      {err != null && <div className="mt-2"><ErrorState error={err} /></div>}
    </div>
  );
}
