/** Helpers for promotions. The API checks the same rules. */

export const MAX_DAYS = 92;
export const MAX_UPLIFT = 10;

export interface PromotionForm {
  name: string;
  start_date: string;
  end_date: string;
  scope: "products" | "category";
  product_ids: string[];
  category: string;
  discount_pct: string;
  uplift: string; // blank = use the estimate
  notes: string;
}

export interface PromotionBody {
  name: string;
  start_date: string;
  end_date: string;
  product_ids: string[];
  category: string | null;
  discount_pct: number | null;
  uplift: number | null;
  use_estimate: boolean;
  notes: string;
}

const ISO = /^\d{4}-\d{2}-\d{2}$/;

function days(a: string, b: string): number {
  return Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86_400_000) + 1;
}

/** The request body for a form, or a plain reason it cannot be sent. */
export function promotionBody(f: PromotionForm): { body: PromotionBody } | { error: string } {
  if (!f.name.trim()) return { error: "Give the promotion a name" };
  if (!ISO.test(f.start_date) || !ISO.test(f.end_date)) return { error: "Choose the start and end dates" };
  if (f.end_date < f.start_date) return { error: "The end date must be on or after the start date" };
  if (days(f.start_date, f.end_date) > MAX_DAYS) return { error: `A promotion can last at most ${MAX_DAYS} days` };
  const ids = [...new Set(f.product_ids.filter(Boolean))];
  if (f.scope === "products" && !ids.length) return { error: "Choose at least one product" };
  if (f.scope === "category" && !f.category) return { error: "Choose a category" };
  let discount: number | null = null;
  if (f.discount_pct.trim()) {
    discount = Number(f.discount_pct);
    if (!Number.isFinite(discount) || discount < 0 || discount > 95) return { error: "Discount must be between 0 and 95%" };
  }
  let uplift: number | null = null;
  if (f.uplift.trim()) {
    uplift = Number(f.uplift);
    if (!Number.isFinite(uplift) || uplift <= 0 || uplift > MAX_UPLIFT) return { error: `Uplift must be above 0 and at most ${MAX_UPLIFT}×` };
  }
  return {
    body: {
      name: f.name.trim(),
      start_date: f.start_date,
      end_date: f.end_date,
      product_ids: f.scope === "products" ? ids : [],
      category: f.scope === "category" ? f.category : null,
      discount_pct: discount,
      uplift,
      use_estimate: uplift === null,
      notes: f.notes.trim(),
    },
  };
}

/** "×2.4 (+140%)" for a lift; "—" when there is none. */
export function fmtLift(x: number | null | undefined): string {
  if (x == null) return "—";
  const pct = Math.round((x - 1) * 100);
  return `×${x.toFixed(2).replace(/\.?0+$/, "")} (${pct >= 0 ? "+" : ""}${pct}%)`;
}

export const STATUS_TONE = { planned: "info", running: "ai", finished: "default" } as const;

export const SOURCE_LABEL: Record<string, string> = {
  planner: "entered by a planner",
  estimated: "estimated from past promotions",
  none: "no uplift: forecasts are not changed",
};
