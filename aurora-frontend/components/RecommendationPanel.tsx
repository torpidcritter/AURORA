import { Badge, Notice, RiskBadge } from "@/components/ui";
import { fmt1, fmtDate, fmtInt, fmtMoney, fmtPct, methodLabel } from "@/lib/format";
import type { Recommendation } from "@/lib/types";

/** Read-only view of one inventory recommendation, including the assumptions behind it. */
export function RecommendationPanel({ rec }: { rec: Recommendation }) {
  if (rec.status !== "ok") {
    return (
      <Notice tone="warn">
        {rec.message ?? (rec.status === "unknown_inventory" ? "On-hand stock is unknown, so no recommendation is made." : "No forecast is available.")}
      </Notice>
    );
  }
  const rows: [string, string, string?][] = [
    ["Mean daily demand", fmt1(rec.mean_daily_demand), `${methodLabel(rec.forecast_method)} forecast from ${fmtDate(rec.forecast_as_of)}`],
    ["Demand error SD (σ)", fmt1(rec.sigma_d), rec.sigma_source],
    ["Service level", fmtPct(rec.service_level, 1), `z = ${rec.z?.toFixed(2)}`],
    ["Lead time", `${fmt1(rec.lead_time_days)} days`, `review cycle ${fmt1(rec.coverage_days)} days`],
    ["Safety stock", fmtInt(rec.safety_stock), "z · σ · √lead time"],
    ["Reorder point", fmtInt(rec.reorder_point), "lead-time demand + safety stock"],
    ["Order-up-to level", fmtInt(rec.order_up_to), "demand over lead time + cycle + safety stock"],
    ["Inventory position", fmtInt(rec.inventory_position), `on hand ${fmtInt(rec.on_hand)} + on order ${fmtInt(rec.on_order)}`],
    ["Days until stockout", rec.days_until_stockout == null ? "beyond horizon" : fmt1(rec.days_until_stockout), "on-hand stock only"],
    ["Stockout risk in lead time", fmtPct(rec.stockout_probability_lead_time, 0), "normal approximation"],
  ];
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <RiskBadge level={rec.risk_level} />
        <span className="text-sm text-muted">Urgency {fmtInt(rec.urgency)} / 100</span>
        {rec.should_reorder ? (
          <Badge tone="warn">
            Order {fmtInt(rec.recommended_order_qty)} units{rec.order_value != null ? ` (${fmtMoney(rec.order_value)})` : ""}
          </Badge>
        ) : (
          <Badge tone="ok">No order needed now</Badge>
        )}
        {!!rec.overstock_units && <Badge tone="info">{fmtInt(rec.overstock_units)} units above order-up-to</Badge>}
      </div>
      {rec.narrative && <p className="mb-3 text-sm text-ink">{rec.narrative}</p>}
      <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
        {rows.map(([k, v, hint]) => (
          <div key={k} className="flex justify-between gap-3 border-b border-line/50 py-1">
            <dt className="text-muted" title={hint}>{k}</dt>
            <dd className="tabular-nums text-ink">{v}</dd>
          </div>
        ))}
      </dl>
      {rec.assumptions.length > 0 && (
        <details className="mt-3 text-xs text-faint">
          <summary className="cursor-pointer">Assumptions ({rec.assumptions.length})</summary>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {rec.assumptions.map((a) => <li key={a}>{a}</li>)}
          </ul>
        </details>
      )}
    </div>
  );
}
