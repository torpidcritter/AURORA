/** Organisation settings form logic. The bounds come from the API (GET /settings/org); these checks only give quick
 *  feedback, and the API validates every value again. */

import { ApiError } from "@/lib/api";
import type { OrgSetting, SettingValue } from "@/lib/types";

export const SETTING_GROUPS: { id: string; title: string; subtitle: string }[] = [
  { id: "inventory", title: "Inventory defaults", subtitle: "Used for products that have no value of their own." },
  { id: "monitoring", title: "Monitoring", subtitle: "Thresholds for demand drift, model age and live forecast accuracy alerts." },
  { id: "alerts", title: "Alert notifications", subtitle: "Which open alerts are sent to the alert channels." },
  { id: "data", title: "Data retention", subtitle: "How long records are kept before the nightly clean-up removes them." },
];

const CHOICE_LABELS: Record<string, string> = {
  mape: "MAPE",
  wape: "WAPE",
  low: "Low",
  medium: "Medium",
  high: "High",
  critical: "Critical",
};
// Fractions that read better with a percentage next to them.
const FRACTION_KEYS = new Set(["default_service_level", "drift_mean_change_threshold"]);

export const choiceLabel = (c: string): string => CHOICE_LABELS[c] ?? c;

export function formatSettingValue(s: Pick<OrgSetting, "key" | "type" | "unit">, v: SettingValue | null | undefined): string {
  if (v == null || v === "") return "—";
  if (s.type === "choice") return choiceLabel(String(v));
  const n = Number(v);
  if (s.unit === "days") return `${n} ${n === 1 ? "day" : "days"}`;
  if (s.unit === "%") return `${n}%`;
  if (FRACTION_KEYS.has(s.key)) return `${n} (${Number((n * 100).toFixed(1))}%)`;
  return String(n);
}

/** "1–365 days, whole number", "Above 0.5, up to 0.999", "0.01–1". Empty for choices. */
export function boundsHint(s: OrgSetting): string {
  if (s.type === "choice" || s.minimum == null || s.maximum == null) return "";
  const unit = s.unit === "days" ? " days" : s.unit === "%" ? "%" : "";
  const range =
    s.minimum_exclusive || s.maximum_exclusive
      ? `${s.minimum_exclusive ? "above" : "from"} ${s.minimum}, ${s.maximum_exclusive ? "below" : "up to"} ${s.maximum}${unit}`
      : `${s.minimum}–${s.maximum}${unit}`;
  const text = s.type === "integer" ? `${range}, whole number` : range;
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function parseSettingInput(s: OrgSetting, raw: string): { value?: SettingValue; error?: string } {
  const t = raw.trim();
  if (s.type === "choice") {
    const choices = s.choices ?? [];
    return choices.includes(t) ? { value: t } : { error: `Choose one of: ${choices.map(choiceLabel).join(", ")}` };
  }
  if (t === "") return { error: "Enter a value, or reset to the default" };
  const n = Number(t);
  if (!Number.isFinite(n)) return { error: "Enter a number" };
  if (s.type === "integer" && !Number.isInteger(n)) return { error: "Enter a whole number" };
  const tooLow = s.minimum != null && (s.minimum_exclusive ? n <= s.minimum : n < s.minimum);
  const tooHigh = s.maximum != null && (s.maximum_exclusive ? n >= s.maximum : n > s.maximum);
  if (tooLow || tooHigh) return { error: `Allowed: ${boundsHint(s)}` };
  return { value: n };
}

/** Unsaved edits per setting key: the input text, or null for "reset to default". */
export type SettingsDraft = Record<string, string | null>;

export function inputValue(s: OrgSetting, draft: SettingsDraft): string {
  if (s.key in draft) {
    const d = draft[s.key];
    return d === null || d === undefined ? String(s.default) : d;
  }
  return String(s.value);
}

/** Validates the draft and builds the PATCH body: only real changes, and null for overrides being reset. */
export function buildSettingsPatch(
  items: OrgSetting[],
  draft: SettingsDraft,
): { body: Record<string, SettingValue | null>; errors: Record<string, string> } {
  const body: Record<string, SettingValue | null> = {};
  const errors: Record<string, string> = {};
  for (const s of items) {
    if (!(s.key in draft)) continue;
    const d = draft[s.key];
    if (d === null || d === undefined) {
      if (s.source === "org") body[s.key] = null;
      continue;
    }
    const r = parseSettingInput(s, d);
    if (r.error !== undefined) errors[s.key] = r.error;
    else if (r.value !== undefined && r.value !== s.value) body[s.key] = r.value;
  }
  return { body, errors };
}

/** Per-field messages from an API validation error (422), keyed by setting key. */
export function fieldErrorsFromApi(e: unknown, keys: string[]): Record<string, string> {
  if (!(e instanceof ApiError) || e.code !== "validation_error" || !Array.isArray(e.details)) return {};
  const out: Record<string, string> = {};
  for (const d of e.details as { loc?: unknown[]; msg?: string }[]) {
    const key = Array.isArray(d?.loc) ? d.loc.map(String).reverse().find((k) => keys.includes(k)) : undefined;
    if (key && d.msg && !out[key]) out[key] = d.msg;
  }
  return out;
}
