import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { buildInventoryPatch } from "@/components/InventoryEditor";
import { StockByLocation } from "@/components/StockByLocation";
import { fetcher } from "@/lib/api";
import { locationErrors, transferErrors } from "@/lib/locations";
import type { LocationStock, Product, ProductStock } from "@/lib/types";

const product: Product = {
  id: "p1",
  sku: "SKU-1",
  name: "Widget",
  category: null,
  unit_cost: 2,
  on_hand: 12,
  on_order: 0,
  inventory_known: true,
  lead_time_days: 7,
  service_level: null,
  pack_size: 1,
  min_order_qty: 0,
  missing_policy: "zero",
  version: 4,
  first_date: null,
  last_date: null,
  history_days: 0,
  units_30d: 0,
  avg_daily_30d: 0,
};

const main: LocationStock = { location_id: "l1", code: "MAIN", name: "Main", is_default: true, is_active: true, on_hand: 10, on_order: 0 };
const store: LocationStock = { location_id: "l2", code: "S1", name: "Store", is_default: false, is_active: true, on_hand: 2, on_order: 0 };

describe("location helpers", () => {
  it("validates location codes and names", () => {
    expect(locationErrors({ code: "WH-2", name: "Second" })).toBeNull();
    expect(locationErrors({ code: "bad code", name: "x" })).toMatch(/Code/);
    expect(locationErrors({ code: "-x", name: "x" })).toMatch(/Code/);
    expect(locationErrors({ code: "OK", name: " " })).toMatch(/Name/);
  });

  it("validates transfers against the stock at the source", () => {
    const levels = [main, store];
    expect(transferErrors({ from: "l1", to: "l2", quantity: "4" }, levels)).toBeNull();
    expect(transferErrors({ from: "", to: "l2", quantity: "4" }, levels)).toMatch(/Choose/);
    expect(transferErrors({ from: "l1", to: "l1", quantity: "4" }, levels)).toMatch(/different/);
    expect(transferErrors({ from: "l1", to: "l2", quantity: "0" }, levels)).toMatch(/whole number/);
    expect(transferErrors({ from: "l2", to: "l1", quantity: "3" }, levels)).toBe("Only 2 on hand at S1");
  });

  it("leaves stock out of the product editor when stock is counted per location", () => {
    const form = { on_hand: "12", on_order: "0", lead_time_days: "9", service_level: "", unit_cost: "2", pack_size: "1", min_order_qty: "0", missing_policy: "zero" as const };
    expect(buildInventoryPatch(form, product).body).toMatchObject({ on_hand: 12, on_order: 0, lead_time_days: 9 });
    const { body } = buildInventoryPatch(form, product, { omitStock: true });
    expect(body).not.toHaveProperty("on_hand");
    expect(body).not.toHaveProperty("on_order");
    expect(body).toMatchObject({ expected_version: 4, lead_time_days: 9 });
  });
});

describe("StockByLocation", () => {
  const fetchMock = vi.fn();
  const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { "content-type": "application/json" } });
  const data: ProductStock = { product_id: "p1", on_hand: 12, on_order: 0, inventory_known: true, locations: [main, store] };
  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => (init?.method && init.method !== "GET" ? json({}, 201) : json(data)));
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());
  const renderIt = (editable = true, onChanged = vi.fn()) =>
    render(
      <SWRConfig value={{ provider: () => new Map(), fetcher, dedupingInterval: 0, shouldRetryOnError: false }}>
        <StockByLocation product={product} editable={editable} onChanged={onChanged} />
      </SWRConfig>,
    );

  it("lists stock per location with the total", async () => {
    renderIt(false);
    const table = await screen.findByRole("table", { name: "Stock by location" });
    expect(within(table).getByText("MAIN")).toBeInTheDocument();
    expect(within(table).getByText("default")).toBeInTheDocument();
    expect(within(table).getByText("12")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Count" })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Transfer stock" })).not.toBeInTheDocument();
  });

  it("saves a count at one location with the product version", async () => {
    const onChanged = vi.fn();
    renderIt(true, onChanged);
    const buttons = await screen.findAllByRole("button", { name: "Count" });
    fireEvent.click(buttons[1]!);
    fireEvent.change(screen.getByLabelText("Counted units at S1"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save count" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH")!;
    expect(call[0]).toBe("/api/proxy/products/p1/inventory");
    expect(JSON.parse(call[1].body)).toEqual({ expected_version: 4, location_id: "l2", on_hand: 5 });
  });

  it("checks a transfer before sending it", async () => {
    const onChanged = vi.fn();
    renderIt(true, onChanged);
    await screen.findByRole("form", { name: "Transfer stock" });
    fireEvent.change(screen.getByLabelText("Move from"), { target: { value: "l2" } });
    fireEvent.change(screen.getByLabelText("Move to"), { target: { value: "l1" } });
    fireEvent.change(screen.getByLabelText("Units"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Transfer" }));
    expect(await screen.findByText("Only 2 on hand at S1")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Units"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Transfer" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === "POST")!;
    expect(call[0]).toBe("/api/proxy/stock/transfers");
    expect(JSON.parse(call[1].body)).toEqual({ product_id: "p1", from_location_id: "l2", to_location_id: "l1", quantity: 2, note: null });
  });
});
