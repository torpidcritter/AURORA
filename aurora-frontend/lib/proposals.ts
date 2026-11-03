import type { AssistantProposal } from "@/lib/types";

/** Plain words for what a decided proposal did, and links to what it created. */
export function proposalOutcome(p: AssistantProposal): { text: string; links: { href: string; label: string }[] } {
  const r = (p.result ?? {}) as Record<string, unknown>;
  if (p.status === "dismissed") return { text: "Dismissed. Nothing was changed.", links: [] };
  if (p.status === "expired") return { text: "Expired. Ask again if you still want this.", links: [] };
  if (p.status === "failed") return { text: `Not done: ${String(r.error ?? "it could not be applied")}. Nothing was changed.`, links: [] };
  if (p.status !== "applied") return { text: "", links: [] };
  if (p.kind === "draft_purchase_orders") {
    const orders = (r.orders as { id: string; number: string }[] | undefined) ?? [];
    const skipped = Object.values((r.skipped as Record<string, number> | undefined) ?? {}).reduce((a, b) => a + b, 0);
    const text = orders.length
      ? `Drafted ${orders.length} purchase order${orders.length === 1 ? "" : "s"}${skipped ? `; ${skipped} product(s) skipped (already on order or nothing to order)` : ""}. Review and approve ${orders.length === 1 ? "it" : "them"} before sending.`
      : "No order was drafted: the products are already on order or need nothing.";
    return { text, links: orders.map((o) => ({ href: `/purchase-orders/${o.id}`, label: o.number })) };
  }
  if (p.kind === "acknowledge_alerts") {
    const n = Number(r.acknowledged ?? 0);
    const s = Number(r.skipped ?? 0);
    return { text: `Acknowledged ${n} alert${n === 1 ? "" : "s"}${s ? `; ${s} had already been closed` : ""}.`, links: [{ href: "/alerts", label: "Alerts" }] };
  }
  return { text: "Override added.", links: r.product_id ? [{ href: `/products/${String(r.product_id)}`, label: "Product" }] : [] };
}
