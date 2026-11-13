import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { buildInventoryPatch } from "@/components/InventoryEditor";
import { RecommendationPanel } from "@/components/RecommendationPanel";
import { ConfirmButton, Pagination } from "@/components/ui";
import type { Product, Recommendation } from "@/lib/types";

const product: Product = {
  id: "p1", sku: "SKU-1", name: "Widget", category: null, unit_cost: 2, on_hand: 10, on_order: 0, inventory_known: true,
  lead_time_days: 7, service_level: 0.95, pack_size: 1, min_order_qty: 0, missing_policy: "zero", version: 3,
  first_date: null, last_date: null, history_days: 100, units_30d: 30, avg_daily_30d: 1,
};
const form = { on_hand: "10", on_order: "0", lead_time_days: "7", service_level: "95", unit_cost: "2", pack_size: "1", min_order_qty: "0", missing_policy: "zero" as const };

describe("buildInventoryPatch", () => {
  it("sends the optimistic-concurrency version and converts service level to a fraction", () => {
    const { body, error } = buildInventoryPatch(form, product);
    expect(error).toBeUndefined();
    expect(body).toMatchObject({ expected_version: 3, on_hand: 10, lead_time_days: 7, service_level: 0.95 });
  });
  it.each([
    [{ on_hand: "-1" }, /On hand/],
    [{ on_hand: "1.5" }, /whole number/],
    [{ lead_time_days: "0" }, /Lead time/],
    [{ service_level: "100" }, /Service level/],
    [{ service_level: "50" }, /Service level/],
    [{ pack_size: "0" }, /Pack size/],
    [{ unit_cost: "abc" }, /Unit cost/],
  ])("rejects %j", (patch, msg) => {
    expect(buildInventoryPatch({ ...form, ...patch }, product).error).toMatch(msg);
  });
  it("omits blank optional fields", () => {
    const { body } = buildInventoryPatch({ ...form, on_hand: "", lead_time_days: "", service_level: "" }, product);
    expect(body).not.toHaveProperty("on_hand");
    expect(body).not.toHaveProperty("lead_time_days");
    expect(body).not.toHaveProperty("service_level");
  });
});

describe("RecommendationPanel", () => {
  it("never shows a quantity when stock is unknown", () => {
    const rec = { product_id: "p1", sku: "S", name: "N", status: "unknown_inventory", risk_level: "unknown", assumptions: [], message: "On-hand stock is unknown." } as Recommendation;
    render(<RecommendationPanel rec={rec} />);
    expect(screen.getByText(/On-hand stock is unknown/)).toBeInTheDocument();
    expect(screen.queryByText(/Order \d/)).toBeNull();
  });
  it("shows the order quantity and assumptions for a reorder", () => {
    const rec = {
      product_id: "p1", sku: "S", name: "N", status: "ok", risk_level: "high", assumptions: ["Open orders excluded"],
      should_reorder: true, recommended_order_qty: 40, order_value: 80, urgency: 71, safety_stock: 5, reorder_point: 12,
    } as Recommendation;
    render(<RecommendationPanel rec={rec} />);
    expect(screen.getByText(/Order 40 units/)).toBeInTheDocument();
    expect(screen.getByText("Open orders excluded")).toBeInTheDocument();
  });
});

describe("ConfirmButton", () => {
  it("runs the action only after confirmation", async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(<ConfirmButton title="Delete alert?" body="Gone for good" confirmLabel="Delete" onConfirm={onConfirm}>Delete</ConfirmButton>);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByText("Gone for good")).toBeInTheDocument();
    const buttons = screen.getAllByRole("button", { name: "Delete", hidden: true });
    fireEvent.click(buttons[buttons.length - 1]!);
    await waitFor(() => expect(onConfirm).toHaveBeenCalledOnce());
  });
  it("cancel does nothing", () => {
    const onConfirm = vi.fn();
    render(<ConfirmButton title="T" body="B" onConfirm={onConfirm}>Go</ConfirmButton>);
    fireEvent.click(screen.getByRole("button", { name: "Go" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel", hidden: true }));
    expect(onConfirm).not.toHaveBeenCalled();
  });
});

describe("Pagination", () => {
  it("moves by one page and disables at the ends", () => {
    const onChange = vi.fn();
    render(<Pagination total={120} limit={50} offset={0} onChange={onChange} />);
    const [prev, next] = screen.getAllByRole("button");
    expect(prev).toBeDisabled();
    fireEvent.click(next!);
    expect(onChange).toHaveBeenCalledWith(50);
  });
});
