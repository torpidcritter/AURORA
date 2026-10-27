import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BudgetPlanView } from "@/components/BudgetPlanView";
import { budgetErrors, parseBudget, planQuantities, shortageAvoided } from "@/lib/budget";
import type { BudgetPlan } from "@/lib/types";

const plan: BudgetPlan = {
  budget: 500,
  policy: "service",
  spent: 480,
  remaining: 20,
  full_cost: 900,
  products_considered: 3,
  products_funded: 2,
  expected_short: { no_orders: 200, this_plan: 50, all_recommended: 10 },
  fill_rate: { no_orders: 0.6, this_plan: 0.9, all_recommended: 0.98 },
  lines: [
    { product_id: "a", sku: "A", name: "Alpha", risk_level: "critical", recommended_qty: 100, unit_cost: 2, planned_qty: 100, value: 200, demand_in_period: 120, expected_short_before: 100, expected_short_after: 5, fill_rate_after: 0.96 },
    { product_id: "b", sku: "B", name: "Beta", risk_level: "high", recommended_qty: 50, unit_cost: 8, planned_qty: 35, value: 280, demand_in_period: 60, expected_short_before: 60, expected_short_after: 20, fill_rate_after: 0.67 },
    { product_id: "c", sku: "C", name: "Gamma", risk_level: "medium", recommended_qty: 10, unit_cost: 50, planned_qty: 0, value: 0, demand_in_period: 20, expected_short_before: 40, expected_short_after: 40, fill_rate_after: 0 },
  ],
  unfunded: [{ product_id: "c", sku: "C", name: "Gamma", recommended_qty: 10, reason: "its smallest order (10 units, 500.00) did not fit the remaining budget" }],
  currency_note: "Costs use each product's unit cost; there is no currency conversion.",
};

describe("budget helpers", () => {
  it("validates and parses the budget", () => {
    expect(budgetErrors("5,000")).toBeNull();
    expect(budgetErrors("0")).toBeNull();
    expect(budgetErrors("")).toMatch(/budget/);
    expect(budgetErrors("-5")).toMatch(/budget/);
    expect(budgetErrors("abc")).toMatch(/budget/);
    expect(parseBudget("12,500.5")).toBe(12500.5);
  });

  it("drafts only planned products and reports the shortage avoided", () => {
    expect(planQuantities(plan)).toEqual({ a: 100, b: 35 });
    expect(shortageAvoided(plan)).toBe(0.75);
    expect(shortageAvoided({ ...plan, expected_short: { no_orders: 0, this_plan: 0, all_recommended: 0 } })).toBeNull();
  });
});

describe("BudgetPlanView", () => {
  it("shows the spend, the fill rate it buys and what was left out", () => {
    render(<BudgetPlanView plan={plan} />);
    expect(screen.getByText("$480")).toBeInTheDocument();
    expect(screen.getByText("90.0%")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
    const rows = within(screen.getByRole("table", { name: "Budget plan" })).getAllByRole("row");
    expect(within(rows[2]!).getByText("partial")).toBeInTheDocument();
    expect(within(rows[1]!).queryByText("partial")).not.toBeInTheDocument();
    expect(screen.getByText(/C: its smallest order/)).toBeInTheDocument();
  });
});
