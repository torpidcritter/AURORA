/** Helpers for category and organisation forecasts (the API does the maths). */

import type { HierarchyForecast } from "@/lib/types";

export const TOTAL = "__total__";
export const NO_CATEGORY = "__none__";

export type Reconcile = "auto" | "bottom_up" | "top_down";

export const RECONCILE_LABEL: Record<"bottom_up" | "top_down", string> = {
  bottom_up: "Sum of product forecasts",
  top_down: "Group model, split across products",
};

/** The request body for a scope picked in the selector. */
export function hierarchyBody(scope: string, horizon: number, reconcile: Reconcile) {
  const base = { horizon, reconcile: reconcile === "auto" ? null : reconcile };
  if (scope === TOTAL) return { level: "total" as const, category: null, ...base };
  return { level: "category" as const, category: scope === NO_CATEGORY ? null : scope, ...base };
}

/** Selector value for a category row from the API. */
export const scopeValue = (category: string | null): string => (category === null ? NO_CATEGORY : category);

/** Units over the horizon, and how much better the recommended method backtested, in WAPE points. */
export function summarise(f: HierarchyForecast) {
  const total = f.points.reduce((s, p) => s + p.yhat, 0);
  const { direct, bottom_up } = f.backtest.wape;
  const gain = direct != null && bottom_up != null ? Math.abs(direct - bottom_up) : null;
  return { total, gain };
}
