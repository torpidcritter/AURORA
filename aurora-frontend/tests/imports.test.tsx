import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ImportSourcesCard, NewImportCard } from "@/components/Imports";
import { EMPTY_FORM, importBody, importFormErrors, scheduleLabel, type ImportSource } from "@/lib/imports";

afterEach(() => vi.unstubAllGlobals());

const source = (over: Partial<ImportSource> = {}): ImportSource => ({
  id: "s1",
  name: "Nightly POS export",
  kind: "file_url",
  url_hint: "https://exports.example.com/…",
  has_credential: true,
  options: { missing_policy: "zero" },
  frequency: "daily",
  hour_utc: 5,
  is_active: true,
  next_run_at: "2026-11-04T05:00:00Z",
  last_run_at: "2026-11-03T05:00:12Z",
  last_status: "imported",
  last_message: "Imported 1,240 rows from sales.csv",
  consecutive_failures: 0,
  version: 3,
  created_at: "2026-10-01T00:00:00Z",
  ...over,
});

const wrap = (ui: ReactNode, data: Record<string, unknown>) =>
  render(<SWRConfig value={{ provider: () => new Map(), fetcher: async (k: string) => data[k], dedupingInterval: 0 }}>{ui}</SWRConfig>);

const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

describe("import form helpers", () => {
  const form = { ...EMPTY_FORM, name: "POS", url: "https://exports.example.com/a.csv" };

  it("accepts a file address and builds the request", () => {
    expect(importFormErrors(form)).toEqual({});
    expect(importBody({ ...form, authorization: " Bearer t " })).toEqual({
      name: "POS",
      kind: "file_url",
      url: "https://exports.example.com/a.csv",
      credential: { authorization: "Bearer t" },
      options: { missing_policy: "zero" },
      frequency: "daily",
      hour_utc: 5,
    });
    expect(importBody(form).credential).toBeNull();
  });

  it("explains what is wrong before sending", () => {
    expect(importFormErrors({ ...EMPTY_FORM })).toMatchObject({ name: expect.any(String), url: "Enter the address" });
    expect(importFormErrors({ ...form, url: "http://x.example.com/a.csv" }).url).toMatch(/https/);
    expect(importFormErrors({ ...form, url: "https://u:p@x.example.com/a.csv" }).url).toMatch(/credentials/);
    expect(importFormErrors({ ...form, authorization: "a\nb" }).authorization).toBe("One line only");
    const woo = { ...form, kind: "woocommerce" as const, url: "https://shop.example.com?x=1", lookbackDays: "120" };
    expect(importFormErrors(woo)).toMatchObject({ url: expect.stringMatching(/store's address only/), consumerKey: expect.any(String), consumerSecret: expect.any(String), lookbackDays: "Between 1 and 90 days" });
  });

  it("sends WooCommerce credentials and the look-back window", () => {
    const body = importBody({ ...form, kind: "woocommerce", url: "https://shop.example.com", consumerKey: "ck_1", consumerSecret: "cs_2", lookbackDays: "30", frequency: "hourly" });
    expect(body.credential).toEqual({ consumer_key: "ck_1", consumer_secret: "cs_2" });
    expect(body.options).toEqual({ lookback_days: 30 });
    expect(body.frequency).toBe("hourly");
  });

  it("describes the schedule", () => {
    expect(scheduleLabel({ frequency: "daily", hour_utc: 5, is_active: true })).toBe("Daily at 05:00 UTC");
    expect(scheduleLabel({ frequency: "hourly", hour_utc: 5, is_active: true })).toBe("Every hour");
    expect(scheduleLabel({ frequency: "hourly", hour_utc: 5, is_active: false })).toBe("Paused");
  });
});

describe("scheduled imports list", () => {
  it("shows each source with its last result and never an address path or credential", async () => {
    wrap(<ImportSourcesCard />, {
      "/imports": [source(), source({ id: "s2", name: "Web store", kind: "woocommerce", options: { lookback_days: 14 }, is_active: false, consecutive_failures: 5, last_status: "failed", last_message: "The store refused access (HTTP 401); check the credential" })],
    });
    const first = await screen.findByRole("listitem", { name: "Import Nightly POS export" });
    expect(first).toHaveTextContent("https://exports.example.com/… · Daily at 05:00 UTC");
    expect(first).toHaveTextContent("Imported 1,240 rows");
    const second = screen.getByRole("listitem", { name: "Import Web store" });
    expect(second).toHaveTextContent("last 14 days re-read");
    expect(second).toHaveTextContent("Paused after 5 failed attempts");
    expect(second).toHaveTextContent("refused access");
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
  });

  it("runs an import now and shows its history", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ run: {}, source: source() }));
    vi.stubGlobal("fetch", fetchMock);
    wrap(<ImportSourcesCard />, {
      "/imports": [source()],
      "/imports/s1/runs": [{ id: "r1", trigger: "manual", status: "unchanged", rows: 0, products: 0, dataset_id: null, message: "The file has not changed since it was last imported", started_at: "2026-11-03T06:00:00Z", finished_at: null }],
    });
    fireEvent.click(await screen.findByRole("button", { name: "Import now" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/imports/s1/run");
    fireEvent.click(screen.getByRole("button", { name: "History" }));
    const table = await screen.findByRole("table", { name: "Import history for Nightly POS export" });
    expect(table).toHaveTextContent("has not changed");
  });

  it("pauses with the version it read", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok(source({ is_active: false })));
    vi.stubGlobal("fetch", fetchMock);
    wrap(<ImportSourcesCard />, { "/imports": [source()] });
    fireEvent.click(await screen.findByRole("button", { name: "Pause" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ expected_version: 3, is_active: false });
  });

  it("deletes, removing imported sales only when asked", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ deleted: true, sales_rows_removed: 12 }));
    vi.stubGlobal("fetch", fetchMock);
    wrap(<ImportSourcesCard />, { "/imports": [source()] });
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    fireEvent.click(screen.getByLabelText("Also delete the sales this import brought in"));
    // jsdom has no showModal, so the open dialog is still "hidden" to role queries.
    fireEvent.click(screen.getByRole("button", { name: "Delete import", hidden: true }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/imports/s1?remove_sales=true");
  });

  it("invites adding one when there are none", async () => {
    wrap(<ImportSourcesCard />, { "/imports": [] });
    expect(await screen.findByText("No scheduled imports yet")).toBeInTheDocument();
  });
});

describe("new import form", () => {
  it("switches to WooCommerce fields and does not send an invalid form", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    wrap(<NewImportCard />, {});
    fireEvent.change(screen.getByLabelText("Source type"), { target: { value: "woocommerce" } });
    expect(screen.getByLabelText("Consumer key")).toBeInTheDocument();
    expect(screen.queryByLabelText("Authorization header (optional)")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Save import" }));
    expect(await screen.findByText("Enter the consumer key")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("saves a file import and clears the form", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(source({ name: "POS" })), { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);
    wrap(<NewImportCard />, {});
    fireEvent.change(screen.getByLabelText("Import name"), { target: { value: "POS" } });
    fireEvent.change(screen.getByLabelText("File address"), { target: { value: "https://exports.example.com/a.csv" } });
    fireEvent.change(screen.getByLabelText("How often"), { target: { value: "hourly" } });
    expect(screen.queryByLabelText("Time (UTC)")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Save import" }));
    expect(await screen.findByText(/Saved “POS”/)).toBeInTheDocument();
    const sent = JSON.parse((fetchMock.mock.calls[0]?.[1] as RequestInit).body as string);
    expect(sent).toMatchObject({ name: "POS", kind: "file_url", frequency: "hourly", credential: null });
    expect(screen.getByLabelText("Import name")).toHaveValue("");
  });
});
