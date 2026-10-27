"use client";

import Link from "next/link";

import { Badge, Notice, RiskBadge, Stat, Table, Td, Th } from "@/components/ui";
import { shortageAvoided } from "@/lib/budget";
import { fmt1, fmtInt, fmtMoney, fmtPct } from "@/lib/format";
import type { BudgetPlan } from "@/lib/types";

/** The result of a budget plan: what to order, what it buys in expected service, and what was left out. */
export function BudgetPlanView({ plan }: { plan: BudgetPlan }) {
  const avoided = shortageAvoided(plan);
  const funded = plan.lines.filter((l) => l.planned_qty > 0);
  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Spend" value={fmtMoney(plan.spent)} hint={`of ${fmtMoney(plan.budget)}; all suggestions cost ${fmtMoney(plan.full_cost)}`} />
        <Stat label="Products ordered" value={`${plan.products_funded} of ${plan.products_considered}`} hint="that need a reorder" />
        <Stat
          label="Expected fill rate"
          value={fmtPct(plan.fill_rate.this_plan, 1)}
          hint={`${fmtPct(plan.fill_rate.no_orders, 1)} with no orders, ${fmtPct(plan.fill_rate.all_recommended, 1)} with every suggestion`}
          tone="ok"
        />
        <Stat label="Shortage avoided" value={fmtPct(avoided, 0)} hint={`${fmt1(plan.expected_short.no_orders - plan.expected_short.this_plan)} of ${fmt1(plan.expected_short.no_orders)} expected short units`} />
      </div>
      <Notice>
        {plan.policy === "service"
          ? "Money goes first to the orders that avoid the most expected shortage per unit spent, a pack at a time."
          : "Products are funded in full in risk order (critical first), skipping orders that no longer fit."}{" "}
        Expected shortage uses the same demand forecast and error as the reorder point. {plan.currency_note}
      </Notice>
      <Table caption="Budget plan">
        <thead>
          <tr>
            <Th>SKU</Th>
            <Th>Risk</Th>
            <Th className="text-right">Suggested</Th>
            <Th className="text-right">Order</Th>
            <Th className="text-right">Value</Th>
            <Th className="text-right">Expected short</Th>
            <Th className="text-right">Fill rate</Th>
          </tr>
        </thead>
        <tbody>
          {plan.lines.map((l) => (
            <tr key={l.product_id}>
              <Td><Link className="font-medium text-primary hover:underline" href={`/products/${l.product_id}`}>{l.sku}</Link> <span className="text-muted">{l.name !== l.sku ? l.name : ""}</span></Td>
              <Td><RiskBadge level={l.risk_level} /></Td>
              <Td className="text-right tabular-nums">{fmtInt(l.recommended_qty)}</Td>
              <Td className="text-right tabular-nums">
                {fmtInt(l.planned_qty)} {l.planned_qty > 0 && l.planned_qty < l.recommended_qty && <Badge tone="warn">partial</Badge>}
              </Td>
              <Td className="text-right tabular-nums">{fmtMoney(l.value)}</Td>
              <Td className="text-right tabular-nums">{fmt1(l.expected_short_before)} → {fmt1(l.expected_short_after)}</Td>
              <Td className="text-right tabular-nums">{fmtPct(l.fill_rate_after, 1)}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
      {plan.unfunded.length > 0 && (
        <div>
          <h3 className="mb-1 text-sm font-medium text-ink">Not ordered</h3>
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted">
            {plan.unfunded.map((u) => <li key={u.product_id}>{u.sku}: {u.reason}</li>)}
          </ul>
        </div>
      )}
      {funded.length === 0 && <Notice tone="warn">Nothing fits this budget.</Notice>}
    </div>
  );
}
