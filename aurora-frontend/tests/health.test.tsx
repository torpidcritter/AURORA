import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ProductsPage from "@/app/(app)/products/page";
import { HealthBadge } from "@/components/HealthBadge";
import { fetcher } from "@/lib/api";
import { buildHealthIndex, matchesHealthFilter, productHealth, type HealthRules } from "@/lib/health";
import type { AccuracyScore, DriftRow, LiveAccuracy, Product } from "@/lib/types";

const rules: HealthRules = { metric: "mape", threshold: 25, minPoints: 14, windowDays: 28 };
const score = (o: Partial<AccuracyScore>): AccuracyScore => ({ points: 20, wape: 10, mape: 12, mape_excluded_zero_days: 0, ...o });
const stable: DriftRow = { product_id: "p", sku: "S", status: "ok", drift: false, ks_statistic: 0.05, reasons: [] };
const drifting: DriftRow = { product_id: "p", sku: "S", status: "ok", drift: true, reasons: ["KS statistic 0.310 > 0.15", "mean changed 40% (> 25%)"] };
const untested: DriftRow = { product_id: "p", sku: "S", status: "insufficient_data", drift: false, reason: "Need at least 7 recent and 28 baseline days" };

describe("productHealth", () => {
  it("is healthy when the alert metric is within the threshold with enough scored days", () => {
    const h = productHealth(score({ mape: 12.34 }), stable, rules);
    expect(h).toMatchObject({ level: "healthy", label: "Healthy", tone: "ok", needsAttention: false });
    expect(h.summary).toContain("Live MAPE 12.3% over 20 days is within the 25% alert threshold.");
    expect(h.summary).toContain("No demand drift.");
  });

  it("is off target when the metric is above the threshold, and that outranks drift", () => {
    const h = productHealth(score({ mape: 41.2 }), drifting, rules);
    expect(h).toMatchObject({ level: "off_target", label: "Off target", tone: "bad", needsAttention: true });
    expect(h.summary).toContain("Live MAPE 41.2% over 20 days is above the 25% alert threshold.");
    expect(h.summary).toContain("Demand drift detected: KS statistic 0.310 > 0.15; mean changed 40% (> 25%).");
  });

  it("flags drift even when accuracy is fine or unknown", () => {
    expect(productHealth(score({}), drifting, rules)).toMatchObject({ level: "drifting", tone: "warn", needsAttention: true });
    expect(productHealth(undefined, drifting, rules)).toMatchObject({ level: "drifting", needsAttention: true });
  });

  it("says 'not enough data' when few days were scored instead of judging on noise", () => {
    const h = productHealth(score({ points: 5, mape: 90 }), stable, rules);
    expect(h).toMatchObject({ level: "insufficient", label: "Not enough data", tone: "default", needsAttention: false });
    expect(h.summary).toContain("Only 5 days of live accuracy so far; at least 14 are needed to judge it.");
  });

  it("does not count zero-demand days towards MAPE's minimum", () => {
    const h = productHealth(score({ points: 20, mape: 80, mape_excluded_zero_days: 10 }), stable, rules);
    expect(h.level).toBe("insufficient");
    expect(h.summary).toContain("Only 10 days of live accuracy so far (10 days with zero demand are skipped by MAPE)");
    // WAPE covers every day, so the same row is judged when WAPE is the alert metric.
    expect(productHealth(score({ points: 20, wape: 30, mape_excluded_zero_days: 10 }), stable, { ...rules, metric: "wape" }).level).toBe("off_target");
  });

  it("treats an undefined metric (all actuals zero) as not enough data", () => {
    expect(productHealth(score({ points: 20, mape: null, wape: null, mape_excluded_zero_days: 0 }), stable, rules).level).toBe("insufficient");
  });

  it("explains missing sources", () => {
    const h = productHealth(undefined, undefined, rules);
    expect(h.level).toBe("insufficient");
    expect(h.summary).toBe("No served forecast has been scored against later sales in the last 28 days. Drift not checked: no sales history.");
    expect(productHealth(undefined, untested, rules).summary).toContain("Drift not tested yet: need at least 7 recent and 28 baseline days.");
  });

  it("uses the threshold exactly: equal to the threshold is still within it", () => {
    expect(productHealth(score({ mape: 25 }), stable, rules).level).toBe("healthy");
    expect(productHealth(score({ mape: 25.01 }), stable, rules).level).toBe("off_target");
  });
});

const accuracy = (by_product: LiveAccuracy["by_product"], o: Partial<LiveAccuracy> = {}): LiveAccuracy => ({
  window_days: 28,
  window_start: "2026-09-01",
  window_end: "2026-09-28",
  sufficient: true,
  min_points: 14,
  points: 60,
  wape: 20,
  mape: 22,
  mape_excluded_zero_days: 0,
  by_method: {},
  by_product,
  alert_metric: "mape",
  alert_threshold: 25,
  ...o,
});

describe("buildHealthIndex / matchesHealthFilter", () => {
  it("indexes by product, counts products needing attention and defaults unknown products", () => {
    const idx = buildHealthIndex(
      accuracy([
        { product_id: "a", sku: "A", ...score({ mape: 50 }) },
        { product_id: "b", sku: "B", ...score({ mape: 10 }) },
      ]),
      { items: [{ ...drifting, product_id: "c" }, { ...stable, product_id: "b" }] },
    );
    expect(idx.get("a").level).toBe("off_target");
    expect(idx.get("b").level).toBe("healthy");
    expect(idx.get("c").level).toBe("drifting");
    expect(idx.get("zzz").level).toBe("insufficient");
    expect(idx.attentionCount).toBe(2);
    expect(idx.rules).toEqual({ metric: "mape", threshold: 25, minPoints: 14, windowDays: 28 });
    expect(matchesHealthFilter(idx.get("a"), "attention")).toBe(true);
    expect(matchesHealthFilter(idx.get("c"), "attention")).toBe(true);
    expect(matchesHealthFilter(idx.get("b"), "attention")).toBe(false);
    expect(matchesHealthFilter(idx.get("b"), "healthy")).toBe(true);
    expect(matchesHealthFilter(idx.get("zzz"), "insufficient")).toBe(true);
    expect(matchesHealthFilter(idx.get("zzz"), "")).toBe(true);
  });

  it("follows the API's alert metric and threshold", () => {
    const idx = buildHealthIndex(accuracy([{ product_id: "a", sku: "A", ...score({ wape: 18, mape: 50 }) }], { alert_metric: "wape", alert_threshold: 15 }), { items: [] });
    expect(idx.get("a").level).toBe("off_target");
    expect(idx.get("a").summary).toContain("Live WAPE 18.0%");
  });
});

describe("HealthBadge", () => {
  it("shows an icon and label, with the explanation as a tooltip and as screen-reader text", () => {
    const h = productHealth(score({ mape: 41.2 }), stable, rules);
    const { container } = render(<HealthBadge health={h} />);
    expect(screen.getByText("Off target")).toBeInTheDocument();
    expect(container.firstElementChild).toHaveAttribute("title", h.summary);
    expect(container.querySelector(".sr-only")).toHaveTextContent(h.summary);
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });
});

const product = (id: string, sku: string): Product => ({
  id, sku, name: `Product ${sku}`, category: null, unit_cost: 1, on_hand: 5, on_order: 0, inventory_known: true, lead_time_days: 7,
  service_level: 0.95, pack_size: 1, min_order_qty: 0, missing_policy: "zero", version: 1, first_date: "2026-01-01", last_date: "2026-09-28",
  history_days: 270, units_30d: 30, avg_daily_30d: 1,
});

describe("products page model health", () => {
  const fetchMock = vi.fn();
  const products = [product("a", "SKU-A"), product("b", "SKU-B"), product("c", "SKU-C")];
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  let driftStatus = 200;

  beforeEach(() => {
    driftStatus = 200;
    fetchMock.mockReset();
    fetchMock.mockImplementation(async (url: string) => {
      const u = new URL(url, "http://x");
      if (u.pathname === "/api/proxy/products") {
        const limit = Number(u.searchParams.get("limit"));
        return json({ items: products, total: products.length, limit, offset: 0 });
      }
      if (u.pathname === "/api/proxy/products/categories") return json([]);
      if (u.pathname === "/api/proxy/intelligence/accuracy") {
        return json(accuracy([{ product_id: "a", sku: "SKU-A", ...score({ mape: 60 }) }, { product_id: "b", sku: "SKU-B", ...score({ mape: 8 }) }]));
      }
      if (u.pathname === "/api/proxy/intelligence/drift") {
        return driftStatus === 200
          ? json({ items: [{ ...stable, product_id: "a" }, { ...stable, product_id: "b" }, { ...untested, product_id: "c" }], drifting: 0 })
          : json({ error: { code: "internal", message: "Drift check failed" } }, driftStatus);
      }
      throw new Error(`unexpected ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  const renderPage = () =>
    render(
      <SWRConfig value={{ provider: () => new Map(), fetcher, dedupingInterval: 0, shouldRetryOnError: false }}>
        <ProductsPage />
      </SWRConfig>,
    );

  it("shows a health badge per row and filters to products needing attention", async () => {
    renderPage();
    const table = await screen.findByRole("table", { name: "Products" });
    await waitFor(() => expect(within(table).getByText("Off target")).toBeInTheDocument());
    expect(within(table).getByText("Healthy")).toBeInTheDocument();
    expect(within(table).getByText("Not enough data")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Model health"), { target: { value: "attention" } });
    await waitFor(() => expect(within(screen.getByRole("table", { name: "Products" })).queryByText("SKU-B")).toBeNull());
    const filtered = screen.getByRole("table", { name: "Products" });
    expect(within(filtered).getByText("SKU-A")).toBeInTheDocument();
    expect(within(filtered).queryByText("SKU-C")).toBeNull();
    // The health filter scans up to the API's page maximum, because the API cannot filter by health.
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("limit=500"))).toBe(true);
    expect(screen.getByRole("option", { name: "Needs attention (1)" })).toBeInTheDocument();
  });

  it("keeps the catalogue usable when health cannot be loaded", async () => {
    driftStatus = 500;
    renderPage();
    const table = await screen.findByRole("table", { name: "Products" });
    expect(await screen.findByText(/Model health could not be loaded/)).toBeInTheDocument();
    expect(within(table).getByText("SKU-A")).toBeInTheDocument();
    expect(within(table).queryByText("Healthy")).toBeNull();
  });
});
