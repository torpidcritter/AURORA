import { render, screen, within } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LiveAccuracyCard, liveAccuracyVerdict } from "@/components/LiveAccuracyCard";
import { fetcher } from "@/lib/api";
import type { LiveAccuracy } from "@/lib/types";

const base: LiveAccuracy = {
  window_days: 28,
  window_start: "2026-09-01",
  window_end: "2026-09-28",
  sufficient: true,
  min_points: 14,
  points: 120,
  wape: 18.4,
  mape: 21.7,
  mape_excluded_zero_days: 6,
  by_method: { sma: { points: 70, wape: 22.1, mape: 25.5, mape_excluded_zero_days: 4 }, transformer: { points: 50, wape: 13.2, mape: 16, mape_excluded_zero_days: 2 } },
  by_product: [
    { product_id: "p1", sku: "SKU-1", points: 28, wape: 40.2, mape: 45.1, mape_excluded_zero_days: 0 },
    { product_id: "p2", sku: "SKU-2", points: 3, wape: 90, mape: 95, mape_excluded_zero_days: 0 },
  ],
  alert_metric: "mape",
  alert_threshold: 25,
};

describe("liveAccuracyVerdict", () => {
  it("is honest when nothing has been scored", () => {
    const v = liveAccuracyVerdict({ ...base, points: 0, sufficient: false, wape: null, mape: null, by_method: {}, by_product: [] });
    expect(v.tone).toBe("info");
    expect(v.text).toMatch(/No served forecast has been scored yet/);
  });
  it("says figures are unreliable and no alert is raised below the minimum", () => {
    const v = liveAccuracyVerdict({ ...base, points: 9, sufficient: false, mape: 80 });
    expect(v).toMatchObject({ tone: "info" });
    expect(v.text).toMatch(/9 of the 14 product-days needed/);
    expect(v.text).toMatch(/no alert is raised/);
  });
  it("compares the configured alert metric with the threshold", () => {
    expect(liveAccuracyVerdict(base)).toMatchObject({ tone: "ok", text: "Live MAPE is 21.7%, within the 25% alert threshold." });
    expect(liveAccuracyVerdict({ ...base, mape: 31 }).tone).toBe("warn");
    expect(liveAccuracyVerdict({ ...base, alert_metric: "wape", alert_threshold: 15 })).toMatchObject({ tone: "warn" });
    expect(liveAccuracyVerdict({ ...base, alert_metric: "wape", alert_threshold: 15 }).text).toMatch(/Live WAPE is 18.4%, above the 15% alert threshold/);
  });
  it("handles an undefined metric", () => {
    expect(liveAccuracyVerdict({ ...base, mape: null }).text).toMatch(/undefined/);
  });
});

describe("LiveAccuracyCard", () => {
  const fetchMock = vi.fn();
  let body: LiveAccuracy = base;
  beforeEach(() => {
    body = base;
    fetchMock.mockReset();
    fetchMock.mockImplementation(async () => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());
  const renderCard = () =>
    render(
      <SWRConfig value={{ provider: () => new Map(), fetcher, dedupingInterval: 0, shouldRetryOnError: false }}>
        <LiveAccuracyCard />
      </SWRConfig>,
    );

  it("shows overall WAPE/MAPE, points, window, alert rule, by-method table and least accurate products", async () => {
    renderCard();
    expect(await screen.findByText("18.4%")).toBeInTheDocument();
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/proxy/intelligence/accuracy");
    expect(screen.getByText("21.7%")).toBeInTheDocument();
    expect(screen.getByText("6 zero-demand days skipped")).toBeInTheDocument();
    expect(screen.getByText("120")).toBeInTheDocument();
    expect(screen.getByText("28 days")).toBeInTheDocument();
    expect(screen.getByText(/Alert rule: live MAPE above 25%/)).toBeInTheDocument();
    const methods = screen.getByRole("table", { name: "Live accuracy by forecasting method" });
    expect(within(methods).getByText("Transformer")).toBeInTheDocument();
    expect(within(methods).getByText("Moving average")).toBeInTheDocument();
    const worst = screen.getByRole("table", { name: "Least accurate products by live WAPE" });
    expect(within(worst).getByRole("link", { name: "SKU-1" })).toHaveAttribute("href", "/products/p1");
    // Products below the minimum number of scored days are not ranked.
    expect(within(worst).queryByText("SKU-2")).toBeNull();
  });

  it("shows an honest note instead of figures to act on when data is insufficient", async () => {
    body = { ...base, points: 5, sufficient: false, by_method: {}, by_product: [] };
    renderCard();
    expect(await screen.findByText(/Not enough data yet: 5 of the 14 product-days needed/)).toBeInTheDocument();
  });
});
