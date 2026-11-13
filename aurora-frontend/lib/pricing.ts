/** Client-side checks for quantity price breaks. The API applies the same rules and is the authority. */

import type { PriceBreak } from "@/lib/types";

export const MAX_BREAKS = 10;

export interface BreakRow {
  min_qty: string;
  unit_cost: string;
}

/** Parse the editor rows into sorted breaks, or return the first problem found. */
export function parseBreaks(rows: BreakRow[], baseCost: number | null): { breaks: PriceBreak[]; error: string | null } {
  const filled = rows.filter((r) => r.min_qty.trim() || r.unit_cost.trim());
  if (filled.length > MAX_BREAKS) return { breaks: [], error: `At most ${MAX_BREAKS} price breaks` };
  if (filled.length && baseCost == null) return { breaks: [], error: "Set the product's unit cost before adding price breaks" };
  const breaks: PriceBreak[] = [];
  for (const r of filled) {
    const q = Number(r.min_qty);
    const c = Number(r.unit_cost);
    if (!Number.isInteger(q) || q <= 1) return { breaks: [], error: "Minimum quantities must be whole numbers above 1" };
    if (!r.unit_cost.trim() || !Number.isFinite(c) || c < 0) return { breaks: [], error: "Enter a unit cost of zero or more for each break" };
    breaks.push({ min_qty: q, unit_cost: c });
  }
  breaks.sort((a, b) => a.min_qty - b.min_qty);
  let prevQty = 1;
  let prevCost = baseCost;
  for (const b of breaks) {
    if (b.min_qty <= prevQty) return { breaks: [], error: "Each break needs a larger minimum quantity than the one before" };
    if (prevCost != null && b.unit_cost >= prevCost) {
      return { breaks: [], error: `The price from ${b.min_qty} units must be lower than the price below it` };
    }
    prevQty = b.min_qty;
    prevCost = b.unit_cost;
  }
  return { breaks, error: null };
}

/** Unit cost for an order of ``qty`` units. */
export function costAt(baseCost: number | null, breaks: PriceBreak[] | null | undefined, qty: number): number | null {
  let cost = baseCost;
  for (const b of breaks ?? []) if (qty >= b.min_qty) cost = b.unit_cost;
  return cost;
}

/** How a supplier's measured lead time compares with what it states, in days (positive means slower). */
export function leadTimeSlip(avg: number | null, stated: number | null): number | null {
  return avg == null || stated == null ? null : Math.round((avg - stated) * 10) / 10;
}
