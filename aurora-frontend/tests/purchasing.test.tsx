import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import OverstockPage from "@/app/(app)/overstock/page";
import { OverridesCard } from "@/components/OverridesCard";
import { PurchaseOrderView } from "@/components/PurchaseOrderView";
import { supplierBody } from "@/components/SupplierForm";
import { fetcher } from "@/lib/api";
import { availableActions, linesPayload, overrideErrors, receivePayload, toDraftLines } from "@/lib/purchasing";
import type { OverstockReport, POLine, PurchaseOrder } from "@/lib/types";

const session = vi.hoisted(() => ({ permissions: [] as string[] }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({ me: { role: "x" }, loading: false, can: (p: string) => session.permissions.includes(p) }),
}));
const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }), usePathname: () => "/" }));

const OPERATOR = ["read", "manage_inventory", "manage_purchasing"];
const ANALYST = [...OPERATOR, "approve_purchase_orders", "manage_overrides"];

const line = (over: Partial<POLine> = {}): POLine => ({
  id: "l1",
  product_id: "p1",
  sku: "SKU-1",
  name: "Widget",
  quantity: 10,
  unit_cost: 2,
  line_value: 20,
  received_qty: 0,
  outstanding_qty: 10,
  suggested_qty: null,
  suggestion: null,
  pack_size: 1,
  min_order_qty: 0,
  ...over,
});

describe("purchasing helpers", () => {
  it("offers only the actions the status and role allow", () => {
    const can = (perms: string[]) => (p: string) => perms.includes(p);
    expect(availableActions("draft", can(["read"]))).toEqual([]);
    expect(availableActions("draft", can(OPERATOR))).toEqual(["edit", "delete"]);
    expect(availableActions("draft", can(ANALYST))).toEqual(["edit", "approve", "delete"]);
    expect(availableActions("approved", can(OPERATOR))).toEqual(["send", "cancel"]);
    expect(availableActions("partially_received", can(OPERATOR))).toEqual(["receive", "cancel"]);
    expect(availableActions("received", can(ANALYST))).toEqual([]);
    expect(availableActions("cancelled", can(ANALYST))).toEqual([]);
  });

  it("validates draft lines; a blank cost means unknown", () => {
    const lines = toDraftLines([line(), line({ product_id: "p2", sku: "SKU-2", unit_cost: null })]);
    expect(lines[1]!.unit_cost).toBe("");
    expect(linesPayload(lines)).toEqual({
      lines: [
        { product_id: "p1", quantity: 10, unit_cost: 2 },
        { product_id: "p2", quantity: 10, unit_cost: null },
      ],
    });
    expect(linesPayload([{ ...lines[0]!, quantity: "2.5" }]).error).toMatch(/whole number/);
    expect(linesPayload([{ ...lines[0]!, quantity: "0" }]).error).toMatch(/at least 1/);
    expect(linesPayload([{ ...lines[0]!, unit_cost: "-1" }]).error).toMatch(/unit cost/);
    expect(linesPayload([lines[0]!, lines[0]!]).error).toMatch(/twice/);
  });

  it("builds a partial receipt and refuses more than is outstanding", () => {
    const lines = [line(), line({ product_id: "p2", sku: "SKU-2", outstanding_qty: 3 })];
    expect(receivePayload(lines, { p1: "4", p2: "" })).toEqual({ lines: [{ product_id: "p1", quantity: 4 }] });
    expect(receivePayload(lines, { p2: "4" }).error).toBe("SKU-2: only 3 still outstanding");
    expect(receivePayload(lines, { p1: "x" }).error).toMatch(/whole number/);
    expect(receivePayload(lines, {}).error).toMatch(/at least one line/);
  });

  it("checks override forms like the API does", () => {
    const ok = { start_date: "2026-07-01", end_date: "2026-07-07", kind: "scale" as const, value: "1.2", reason: "Promo" };
    expect(overrideErrors(ok)).toEqual({});
    expect(overrideErrors({ ...ok, end_date: "2026-06-30" }).end_date).toMatch(/on or after/);
    expect(overrideErrors({ ...ok, end_date: "2027-07-03" }).end_date).toMatch(/366/);
    expect(overrideErrors({ ...ok, value: "11" }).value).toMatch(/at most 10/);
    expect(overrideErrors({ ...ok, kind: "set", value: "11" })).toEqual({});
    expect(overrideErrors({ ...ok, value: "-1" }).value).toBeDefined();
    expect(overrideErrors({ ...ok, reason: " " }).reason).toBeDefined();
  });

  it("turns the supplier form into an API body", () => {
    const v = { name: " Acme ", contact_name: "", email: "a@acme.example", phone: "", lead_time_days: "12", min_order_value: "", notes: "" };
    expect(supplierBody(v).body).toEqual({ name: "Acme", contact_name: null, email: "a@acme.example", phone: null, lead_time_days: 12, min_order_value: null, notes: null });
    expect(supplierBody({ ...v, name: " " }).error).toMatch(/Name/);
    expect(supplierBody({ ...v, lead_time_days: "0" }).error).toMatch(/Lead time/);
    expect(supplierBody({ ...v, email: "nope" }).error).toMatch(/e-mail/);
  });
});

// --- components -------------------------------------------------------------------------------------------------------

const fetchMock = vi.fn();
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const withSWR = (ui: React.ReactNode) =>
  render(<SWRConfig value={{ provider: () => new Map(), fetcher, dedupingInterval: 0, shouldRetryOnError: false }}>{ui}</SWRConfig>);

beforeEach(() => {
  fetchMock.mockReset();
  push.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

const order = (over: Partial<PurchaseOrder> = {}): PurchaseOrder => ({
  id: "po1",
  number: "PO-000007",
  status: "draft",
  supplier_id: "s1",
  supplier_name: "Acme",
  supplier_email: null,
  expected_date: null,
  notes: null,
  source: "suggested",
  created_by: null,
  approved_by: null,
  created_at: "2026-10-01T10:00:00Z",
  approved_at: null,
  sent_at: null,
  received_at: null,
  cancelled_at: null,
  version: 3,
  line_count: 1,
  total_units: 10,
  total_value: 20,
  total_complete: true,
  lines: [line({ suggested_qty: 12, suggestion: { risk_level: "high", days_until_stockout: 2.25, overrides_applied: 1 } })],
  warnings: ["Order value 20.00 is below Acme's minimum of 100.00"],
  ...over,
});

describe("purchase order page", () => {
  const renderPage = () =>
    withSWR(<PurchaseOrderView id="po1" />);

  it("shows lines, why they were suggested and warnings; operators cannot approve", async () => {
    session.permissions = OPERATOR;
    fetchMock.mockResolvedValue(json(order()));
    renderPage();
    expect(await screen.findByText(/PO-000007 · Acme/)).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Lines of PO-000007" });
    expect(within(table).getByText(/Suggested 12 \(risk high\) · 2.3 days of stock left · planner override applied · changed to 10/)).toBeInTheDocument();
    expect(screen.getByText(/below Acme's minimum/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download CSV" })).toHaveAttribute("href", "/api/proxy/purchase-orders/po1/export.csv");
  });

  it("approves with the version it showed", async () => {
    session.permissions = ANALYST;
    fetchMock.mockResolvedValueOnce(json(order())).mockResolvedValueOnce(json(order({ status: "approved", version: 4 })));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() => expect(screen.getByText("Approved")).toBeInTheDocument());
    const [url, init] = fetchMock.mock.calls[1]!;
    expect(url).toBe("/api/proxy/purchase-orders/po1/approve");
    expect(JSON.parse(init.body)).toEqual({ expected_version: 3 });
    expect(screen.getByRole("button", { name: "Mark as sent" })).toBeInTheDocument();
  });

  it("receives part of a sent order", async () => {
    session.permissions = OPERATOR;
    const sent = order({ status: "sent", version: 5 });
    fetchMock.mockResolvedValueOnce(json(sent)).mockResolvedValueOnce(json(order({ status: "partially_received", version: 6 })));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Receive goods" }));
    const input = screen.getByLabelText("Received quantity for SKU-1");
    expect(input).toHaveValue("10");
    fireEvent.change(input, { target: { value: "11" } });
    fireEvent.click(screen.getByRole("button", { name: "Book into stock" }));
    expect(await screen.findByText("SKU-1: only 10 still outstanding")).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Book into stock" }));
    await waitFor(() => expect(screen.getByText("Partly received")).toBeInTheDocument());
    expect(JSON.parse(fetchMock.mock.calls[1]![1].body)).toEqual({ expected_version: 5, lines: [{ product_id: "p1", quantity: 4 }] });
  });
});

describe("overrides card", () => {
  it("validates, then creates an override with the typed values", async () => {
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) =>
      init?.method === "POST" ? json({ id: "o1" }, 201) : json({ items: [], total: 0, limit: 50, offset: 0 }),
    );
    const onChanged = vi.fn();
    withSWR(<OverridesCard productId="p1" editable onChanged={onChanged} />);
    expect(await screen.findByText("No active overrides.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add override" }));
    expect(await screen.findByText("Pick a start date")).toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([, init]) => !init || init.method !== "POST")).toBe(true);

    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-11-20" } });
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-11-30" } });
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "set" } });
    fireEvent.change(screen.getByLabelText("Units per day"), { target: { value: "40" } });
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: " Black Friday listing " } });
    fireEvent.click(screen.getByRole("button", { name: "Add override" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST")!;
    expect(post[0]).toBe("/api/proxy/forecast-overrides");
    expect(JSON.parse(post[1].body)).toEqual({
      product_id: "p1",
      start_date: "2026-11-20",
      end_date: "2026-11-30",
      kind: "set",
      value: 40,
      reason: "Black Friday listing",
    });
  });

  it("is read only without the permission", async () => {
    fetchMock.mockResolvedValue(
      json({
        items: [{ id: "o1", product_id: "p1", sku: "A", name: "A", start_date: "2026-11-20", end_date: "2026-11-30", kind: "scale", value: 1.5, reason: "Promo", created_by: null, created_at: null, archived_at: null }],
        total: 1,
        limit: 50,
        offset: 0,
      }),
    );
    withSWR(<OverridesCard productId="p1" editable={false} />);
    expect(await screen.findByText("forecast × 1.5")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Archive" })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Add an override" })).not.toBeInTheDocument();
  });
});

describe("overstock page", () => {
  const report: OverstockReport = {
    as_of: "2026-06-30",
    cover_days: 180,
    dead_days: 90,
    trailing_days: 90,
    summary: { products: 2, dead: 1, overstock: 1, excess_value: 1640, stock_value: 2000, missing_cost: 1 },
    items: [
      { product_id: "p1", sku: "SLOW", name: "Slow", category: null, status: "overstock", on_hand: 1000, on_order: 50, units_trailing: 90, avg_daily_demand: 1, days_of_cover: 1000, last_sale_date: "2026-06-30", days_since_last_sale: 0, excess_units: 820, unit_cost: 2, stock_value: 2000, excess_value: 1640, suggestions: ["About 1,000 days of stock."] },
      { product_id: "p2", sku: "DEAD", name: "Dead", category: null, status: "dead", on_hand: 30, on_order: 0, units_trailing: 0, avg_daily_demand: 0, days_of_cover: null, last_sale_date: null, days_since_last_sale: null, excess_units: 30, unit_cost: null, stock_value: null, excess_value: null, suggestions: ["No recorded sales yet."] },
    ],
    total: 2,
    limit: 100,
    offset: 0,
  };

  it("shows the summary and rows, and refetches with a new threshold", async () => {
    fetchMock.mockResolvedValue(json(report));
    withSWR(<OverstockPage />);
    const table = await screen.findByRole("table", { name: "Overstocked and dead-stock products" });
    expect(within(table).getByText("Dead stock")).toBeInTheDocument();
    expect(within(table).getByText("+50 on order")).toBeInTheDocument();
    expect(within(table).getByText("never")).toBeInTheDocument();
    expect(screen.getByText(/1 flagged product\(s\) have no unit cost/)).toBeInTheDocument();
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/proxy/inventory/overstock?cover_days=180&dead_days=90&limit=100&offset=0");
    fireEvent.change(screen.getByLabelText("Excess beyond"), { target: { value: "90" } });
    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u).includes("cover_days=90&"))).toBe(true));
  });
});
