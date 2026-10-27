import { render, screen, within } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { HierarchyResult } from "@/components/HierarchyResult";
import { hierarchyBody, NO_CATEGORY, scopeValue, summarise, TOTAL } from "@/lib/hierarchy";
import type { HierarchyForecast } from "@/lib/types";

const f: HierarchyForecast = {
  level: "category",
  category: "Tea",
  label: "Tea",
  as_of: "2026-06-28",
  horizon: 2,
  coverage: 0.9,
  products: 3,
  products_forecast: 2,
  excluded: [{ product_id: "p3", sku: "TEA-NEW", reason: "no sales history" }],
  reconcile: "top_down",
  recommended: "bottom_up",
  backtest: { folds: 4, wape: { direct: 18.2, bottom_up: 12.5 } },
  direct_method: "holt_winters",
  interval: { calibrated: true, empirical_coverage: 0.9, note: "Conformal interval from the group model's recent errors." },
  points: [
    { date: "2026-06-29", yhat: 30, lower: 25, upper: 35, bottom_up: 28, direct: 30 },
    { date: "2026-06-30", yhat: 10, lower: 8, upper: 12, bottom_up: 12, direct: 10 },
  ],
  history: [{ date: "2026-06-28", units: 29 }],
  breakdown: [
    { product_id: "p1", sku: "TEA-1", name: "Green", category: "Tea", method: "ses", overrides: 1, units: 30, own_forecast_units: 28, share: 0.75 },
    { product_id: "p2", sku: "TEA-2", name: "Black", category: "Tea", method: "sma", overrides: 0, units: 10, own_forecast_units: 12, share: 0.25 },
  ],
  warnings: ["Some products use the Transformer"],
};

describe("hierarchy helpers", () => {
  it("builds request bodies for each kind of group", () => {
    expect(hierarchyBody(TOTAL, 28, "auto")).toEqual({ level: "total", category: null, horizon: 28, reconcile: null });
    expect(hierarchyBody(NO_CATEGORY, 7, "bottom_up")).toEqual({ level: "category", category: null, horizon: 7, reconcile: "bottom_up" });
    expect(hierarchyBody("Tea", 14, "top_down")).toEqual({ level: "category", category: "Tea", horizon: 14, reconcile: "top_down" });
    expect(scopeValue(null)).toBe(NO_CATEGORY);
    expect(scopeValue("Tea")).toBe("Tea");
  });

  it("sums the horizon and the accuracy difference", () => {
    expect(summarise(f)).toEqual({ total: 40, gain: expect.closeTo(5.7, 5) });
  });
});

describe("HierarchyResult", () => {
  beforeAll(() => {
    // Recharts measures its container; jsdom has no ResizeObserver.
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  });

  it("shows the products adding up to the total and which method was more accurate", () => {
    render(<HierarchyResult f={f} />);
    expect(screen.getByText("40 units")).toBeInTheDocument();
    expect(screen.getByText(/Sum of product forecasts had the lower error over 4 backtest periods/)).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Product breakdown" });
    const rows = within(table).getAllByRole("row");
    expect(within(rows[1]!).getByText("TEA-1")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("75.0%")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("override")).toBeInTheDocument();
    expect(within(rows[3]!).getByText("40")).toBeInTheDocument();
    expect(screen.getByText(/TEA-NEW \(no sales history\)/)).toBeInTheDocument();
    expect(screen.getByText("Some products use the Transformer")).toBeInTheDocument();
  });
});
