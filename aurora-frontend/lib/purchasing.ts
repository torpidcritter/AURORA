/** Pure helpers for purchase orders and forecast overrides. The API enforces every rule; these only give early,
 *  friendly feedback and decide which buttons to show. */

import type { FvaVerdict, OverrideKind, POLine, POStatus } from "@/lib/types";

export const PO_STATUS_LABEL: Record<POStatus, string> = {
  draft: "Draft",
  approved: "Approved",
  sent: "Sent",
  partially_received: "Partly received",
  received: "Received",
  cancelled: "Cancelled",
};

export const PO_STATUS_TONE: Record<POStatus, "default" | "info" | "warn" | "ok" | "bad" | "ai"> = {
  draft: "default",
  approved: "info",
  sent: "ai",
  partially_received: "warn",
  received: "ok",
  cancelled: "bad",
};

export type POAction = "edit" | "approve" | "send" | "receive" | "cancel" | "delete";

/** Actions the order's status allows, filtered by the caller's permissions. */
export function availableActions(status: POStatus, can: (perm: string) => boolean): POAction[] {
  const out: POAction[] = [];
  const manage = can("manage_purchasing");
  if (status === "draft") {
    if (manage) out.push("edit");
    if (can("approve_purchase_orders")) out.push("approve");
    if (manage) out.push("delete");
  }
  if (status === "approved" && manage) out.push("send", "cancel");
  if ((status === "sent" || status === "partially_received") && manage) out.push("receive", "cancel");
  return out;
}

export interface DraftLine {
  product_id: string;
  sku: string;
  name?: string;
  quantity: string;
  unit_cost: string;
}

export function toDraftLines(lines: POLine[]): DraftLine[] {
  return lines.map((l) => ({
    product_id: l.product_id,
    sku: l.sku,
    name: l.name,
    quantity: String(l.quantity),
    unit_cost: l.unit_cost == null ? "" : String(l.unit_cost),
  }));
}

/** Validate edited lines. An empty cost means "unknown" (sent as null). */
export function linesPayload(lines: DraftLine[]): { lines?: { product_id: string; quantity: number; unit_cost: number | null }[]; error?: string } {
  const seen = new Set<string>();
  const out = [];
  for (const l of lines) {
    if (seen.has(l.product_id)) return { error: `${l.sku} is on the order twice` };
    seen.add(l.product_id);
    const q = Number(l.quantity);
    if (!/^\d+$/.test(l.quantity.trim()) || q < 1 || q > 10_000_000) return { error: `${l.sku}: quantity must be a whole number of at least 1` };
    let cost: number | null = null;
    if (l.unit_cost.trim() !== "") {
      cost = Number(l.unit_cost);
      if (!Number.isFinite(cost) || cost < 0 || cost > 1e7) return { error: `${l.sku}: unit cost must be a number of 0 or more` };
    }
    out.push({ product_id: l.product_id, quantity: q, unit_cost: cost });
  }
  if (out.length > 500) return { error: "An order can have at most 500 lines" };
  return { lines: out };
}

/** Build the receive request from per-line inputs; blank inputs are skipped. */
export function receivePayload(lines: POLine[], inputs: Record<string, string>): { lines?: { product_id: string; quantity: number }[]; error?: string } {
  const out = [];
  for (const l of lines) {
    const raw = (inputs[l.product_id] ?? "").trim();
    if (raw === "" || raw === "0") continue;
    if (!/^\d+$/.test(raw)) return { error: `${l.sku}: enter a whole number` };
    const q = Number(raw);
    if (q > l.outstanding_qty) return { error: `${l.sku}: only ${l.outstanding_qty} still outstanding` };
    out.push({ product_id: l.product_id, quantity: q });
  }
  if (out.length === 0) return { error: "Enter a quantity for at least one line" };
  return { lines: out };
}

// --- overrides --------------------------------------------------------------------------------------------------------

export interface OverrideForm {
  start_date: string;
  end_date: string;
  kind: OverrideKind;
  value: string;
  reason: string;
}

export const OVERRIDE_MAX_DAYS = 366;

export function overrideErrors(f: OverrideForm): Partial<Record<keyof OverrideForm, string>> {
  const e: Partial<Record<keyof OverrideForm, string>> = {};
  if (!f.start_date) e.start_date = "Pick a start date";
  if (!f.end_date) e.end_date = "Pick an end date";
  if (f.start_date && f.end_date) {
    const days = (Date.parse(f.end_date) - Date.parse(f.start_date)) / 86_400_000 + 1;
    if (days < 1) e.end_date = "End must be on or after the start";
    else if (days > OVERRIDE_MAX_DAYS) e.end_date = `At most ${OVERRIDE_MAX_DAYS} days`;
  }
  const v = Number(f.value);
  if (f.value.trim() === "" || !Number.isFinite(v) || v < 0) e.value = "Enter a number of 0 or more";
  else if (f.kind === "scale" && v > 10) e.value = "A factor can be at most 10";
  else if (f.kind === "set" && v > 10_000_000) e.value = "Too large";
  if (!f.reason.trim()) e.reason = "Say why, so the override can be reviewed later";
  return e;
}

export function describeOverride(kind: OverrideKind, value: number): string {
  return kind === "set" ? `${value.toLocaleString()} units/day` : `forecast × ${value}`;
}

export const FVA_LABEL: Record<FvaVerdict, string> = {
  helped: "Helped",
  hurt: "Hurt",
  no_change: "No real change",
  pending: "Not scored yet",
};

export const FVA_TONE: Record<FvaVerdict, "ok" | "bad" | "default" | "info"> = {
  helped: "ok",
  hurt: "bad",
  no_change: "default",
  pending: "info",
};
