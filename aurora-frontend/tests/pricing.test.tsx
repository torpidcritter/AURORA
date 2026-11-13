import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PriceBreaksEditor } from "@/components/PriceBreaksEditor";
import { RecommendationPanel } from "@/components/RecommendationPanel";
import { costAt, leadTimeSlip, parseBreaks } from "@/lib/pricing";
import type { Product, Recommendation } from "@/lib/types";

const row = (min_qty: string, unit_cost: string) => ({ min_qty, unit_cost });

describe("price break rules", () => {
  it("sorts valid breaks and prices orders by tier", () => {
    const { breaks, error } = parseBreaks([row("100", "1.8"), row("50", "1.9"), row("", "")], 2);
    expect(error).toBeNull();
    expect(breaks).toEqual([{ min_qty: 50, unit_cost: 1.9 }, { min_qty: 100, unit_cost: 1.8 }]);
    expect([1, 49, 50, 99, 100].map((q) => costAt(2, breaks, q))).toEqual([2, 2, 1.9, 1.9, 1.8]);
    expect(costAt(null, [], 10)).toBeNull();
  });

  it("refuses the same rules as the API", () => {
    expect(parseBreaks([row("50", "2")], 2).error).toMatch(/lower/);
    expect(parseBreaks([row("50", "1.5"), row("100", "1.6")], 2).error).toMatch(/lower/);
    expect(parseBreaks([row("50", "1.5"), row("50", "1.4")], 2).error).toMatch(/larger/);
    expect(parseBreaks([row("1", "1.5")], 2).error).toMatch(/above 1/);
    expect(parseBreaks([row("2.5", "1.5")], 2).error).toMatch(/whole/);
    expect(parseBreaks([row("50", "")], 2).error).toMatch(/unit cost/);
    expect(parseBreaks([row("50", "1")], null).error).toMatch(/unit cost before/);
    expect(parseBreaks([], null).error).toBeNull();
  });

  it("describes lead-time slip", () => {
    expect(leadTimeSlip(8, 7)).toBe(1);
    expect(leadTimeSlip(5.2, 7)).toBe(-1.8);
    expect(leadTimeSlip(null, 7)).toBeNull();
  });
});

const product = { id: "p1", sku: "A", name: "A", unit_cost: 2, version: 3, price_breaks: [{ min_qty: 50, unit_cost: 1.9 }] } as Product;

describe("PriceBreaksEditor", () => {
  it("lists breaks read-only for viewers", () => {
    render(<PriceBreaksEditor product={product} editable={false} onSaved={() => {}} />);
    expect(screen.getByText(/From 50 units: 1.9 each/)).toBeInTheDocument();
  });

  it("blocks saving an invalid tier", () => {
    render(<PriceBreaksEditor product={product} editable onSaved={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Add price break" }));
    fireEvent.change(screen.getByLabelText("Break 2 from units"), { target: { value: "100" } });
    fireEvent.change(screen.getByLabelText("Break 2 unit cost"), { target: { value: "1.95" } });
    expect(screen.getByText(/price from 100 units must be lower/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save price breaks" })).toBeDisabled();
  });
});

describe("RecommendationPanel price hint", () => {
  it("shows the next break without applying it", () => {
    const rec = {
      product_id: "p1", sku: "A", name: "A", status: "ok", risk_level: "high", assumptions: [], should_reorder: true,
      recommended_order_qty: 80, order_value: 152, order_unit_cost: 1.9,
      price_break: { min_qty: 100, unit_cost: 1.8, extra_units: 20, order_value_now: 152, order_value_at_break: 180, unit_saving: 0.1 },
    } as Recommendation;
    render(<RecommendationPanel rec={rec} />);
    expect(screen.getByText(/at \$1.90 each/)).toBeInTheDocument();
    expect(screen.getByText(/Ordering 20 more \(100 units\) reaches the \$1.80 price/)).toBeInTheDocument();
  });
});
