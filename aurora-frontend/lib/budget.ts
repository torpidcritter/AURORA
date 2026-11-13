/** Helpers for the budget planner (the API does the optimisation). */

import type { BudgetPlan } from "@/lib/types";

export function budgetErrors(raw: string): string | null {
  const v = Number(raw.replace(/,/g, ""));
  if (!raw.trim() || !Number.isFinite(v) || v < 0) return "Enter a budget of zero or more";
  return null;
}

export const parseBudget = (raw: string): number => Number(raw.replace(/,/g, ""));

/** Planned quantities keyed by product, for drafting purchase orders. Products with nothing planned are left out. */
export function planQuantities(plan: BudgetPlan): Record<string, number> {
  return Object.fromEntries(plan.lines.filter((l) => l.planned_qty > 0).map((l) => [l.product_id, l.planned_qty]));
}

/** Share of the units that would have been short with no orders that this plan avoids. */
export function shortageAvoided(plan: BudgetPlan): number | null {
  const { no_orders, this_plan } = plan.expected_short;
  return no_orders > 0 ? (no_orders - this_plan) / no_orders : null;
}
