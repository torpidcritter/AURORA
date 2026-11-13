/**
 * Per-product model health, computed in the browser from two org-wide endpoints:
 *   - GET /intelligence/accuracy: served forecasts scored against the sales that arrived later (by_product),
 *   - GET /intelligence/drift: KS test + mean change, last 30 days against up to 180 prior days.
 * The API has no per-product health field, so the rule lives here. It reuses the API's own alert metric, alert
 * threshold and minimum number of scored days, so a product is "off target" by the same standard as the org-wide
 * live-accuracy alert.
 */

import type { AccuracyScore, DriftRow, LiveAccuracy } from "@/lib/types";

export type HealthLevel = "off_target" | "drifting" | "healthy" | "insufficient";
export type HealthTone = "bad" | "warn" | "ok" | "default";

export interface ProductHealth {
  level: HealthLevel;
  label: string;
  tone: HealthTone;
  needsAttention: boolean;
  /** Plain-language explanation, used for the badge's tooltip and screen-reader text. */
  summary: string;
}

export interface HealthRules {
  metric: "mape" | "wape";
  threshold: number;
  minPoints: number;
  windowDays: number;
}

export const HEALTH_LABELS: Record<HealthLevel, { label: string; tone: HealthTone }> = {
  off_target: { label: "Off target", tone: "bad" },
  drifting: { label: "Drifting", tone: "warn" },
  healthy: { label: "Healthy", tone: "ok" },
  insufficient: { label: "Not enough data", tone: "default" },
};

export function healthRules(acc: Pick<LiveAccuracy, "alert_metric" | "alert_threshold" | "min_points" | "window_days">): HealthRules {
  return { metric: acc.alert_metric, threshold: acc.alert_threshold, minPoints: acc.min_points, windowDays: acc.window_days };
}

const pct = (v: number) => `${v.toFixed(1)}%`;
const days = (n: number) => `${n} day${n === 1 ? "" : "s"}`;

type AccuracyState = "poor" | "good" | "unknown";
type DriftState = "drift" | "stable" | "unknown";

function judgeAccuracy(row: AccuracyScore | undefined, r: HealthRules): { state: AccuracyState; text: string } {
  const name = r.metric.toUpperCase();
  if (!row || row.points === 0) {
    return { state: "unknown", text: `No served forecast has been scored against later sales in the last ${days(r.windowDays)}.` };
  }
  // MAPE is undefined on zero-demand days, so only the days it actually covers count towards the minimum.
  const skipped = r.metric === "mape" ? row.mape_excluded_zero_days : 0;
  const usable = row.points - skipped;
  const skippedNote = skipped > 0 ? ` (${days(skipped)} with zero demand are skipped by MAPE)` : "";
  const value = row[r.metric];
  if (usable < r.minPoints || value == null) {
    return {
      state: "unknown",
      text: `Only ${days(usable)} of live accuracy so far${skippedNote}; at least ${r.minPoints} are needed to judge it.`,
    };
  }
  if (value > r.threshold) {
    return { state: "poor", text: `Live ${name} ${pct(value)} over ${days(row.points)} is above the ${r.threshold}% alert threshold.` };
  }
  return { state: "good", text: `Live ${name} ${pct(value)} over ${days(row.points)} is within the ${r.threshold}% alert threshold.` };
}

function judgeDrift(row: DriftRow | undefined): { state: DriftState; text: string } {
  if (!row) return { state: "unknown", text: "Drift not checked: no sales history." };
  if (row.drift) {
    const why = (row.reasons ?? (row.reason ? [row.reason] : [])).join("; ");
    return { state: "drift", text: `Demand drift detected${why ? `: ${why}` : ""}.` };
  }
  if (row.status === "ok") return { state: "stable", text: "No demand drift." };
  return { state: "unknown", text: `Drift not tested yet${row.reason ? `: ${row.reason.charAt(0).toLowerCase()}${row.reason.slice(1)}` : ""}.` };
}

/** Health of one product. Accuracy above the threshold outranks drift; drift alone also needs attention. */
export function productHealth(acc: AccuracyScore | undefined, drift: DriftRow | undefined, rules: HealthRules): ProductHealth {
  const a = judgeAccuracy(acc, rules);
  const d = judgeDrift(drift);
  const level: HealthLevel = a.state === "poor" ? "off_target" : d.state === "drift" ? "drifting" : a.state === "good" ? "healthy" : "insufficient";
  return { level, ...HEALTH_LABELS[level], needsAttention: level === "off_target" || level === "drifting", summary: `${a.text} ${d.text}` };
}

export interface HealthIndex {
  rules: HealthRules;
  get: (productId: string) => ProductHealth;
  attentionCount: number;
}

/** Health for every product, keyed by product id. Products missing from both sources get "Not enough data". */
export function buildHealthIndex(acc: LiveAccuracy, drift: { items: DriftRow[] }): HealthIndex {
  const rules = healthRules(acc);
  const accBy = new Map(acc.by_product.map((r) => [r.product_id, r]));
  const driftBy = new Map(drift.items.map((r) => [r.product_id, r]));
  const cache = new Map<string, ProductHealth>();
  const get = (id: string): ProductHealth => {
    let h = cache.get(id);
    if (!h) {
      h = productHealth(accBy.get(id), driftBy.get(id), rules);
      cache.set(id, h);
    }
    return h;
  };
  const ids = new Set([...accBy.keys(), ...driftBy.keys()]);
  let attentionCount = 0;
  for (const id of ids) if (get(id).needsAttention) attentionCount += 1;
  return { rules, get, attentionCount };
}

export type HealthFilter = "" | "attention" | "healthy" | "insufficient";

export function matchesHealthFilter(h: ProductHealth, f: HealthFilter): boolean {
  if (f === "") return true;
  if (f === "attention") return h.needsAttention;
  return h.level === f;
}
