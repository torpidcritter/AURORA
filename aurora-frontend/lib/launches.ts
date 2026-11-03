/** Helpers for launch plans. The API checks the same rules. */

export const MAX_ANALOGUES = 5;

export interface LaunchForm {
  launch_date: string;
  analogue_ids: string[];
  scale_pct: string;
  notes: string;
}

/** The request body for a form, or a plain reason it cannot be sent. */
export function launchBody(f: LaunchForm): { body: { launch_date: string; analogue_ids: string[]; scale: number; notes: string } } | { error: string } {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(f.launch_date)) return { error: "Choose the launch date" };
  const ids = [...new Set(f.analogue_ids.filter(Boolean))];
  if (!ids.length) return { error: "Choose at least one similar product" };
  if (ids.length > MAX_ANALOGUES) return { error: `Choose at most ${MAX_ANALOGUES} similar products` };
  const pct = Number(f.scale_pct);
  if (!Number.isFinite(pct) || pct < 5 || pct > 2000) return { error: "Expected sales must be between 5% and 2000% of the similar products" };
  return { body: { launch_date: f.launch_date, analogue_ids: ids, scale: Math.round(pct) / 100, notes: f.notes.trim() } };
}

/** Plain words for how far the launch has got. */
export function launchStage(ownDays: number, handoverDays: number, launchDate: string, today: string): string {
  if (ownDays >= handoverDays) return "Selling long enough: normal forecasting is used";
  if (ownDays > 0) return `${ownDays} day(s) of own sales are correcting the plan`;
  return launchDate > today ? "Not launched yet" : "Launched, waiting for the first sales";
}
