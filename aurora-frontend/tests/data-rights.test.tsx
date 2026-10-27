import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DataExportCard, OrgDeletionCard } from "@/components/DataRights";
import { daysUntil, exportRows, exportState } from "@/lib/dataRights";
import type { Job } from "@/lib/types";

vi.mock("@/lib/session", () => ({
  useSession: () => ({ me: { org_name: "Acme" }, loading: false, can: () => true }),
}));

afterEach(() => vi.unstubAllGlobals());

const NOW = new Date("2026-10-27T12:00:00Z");
const job = (over: Partial<Job>): Job => ({
  id: "j1",
  kind: "data_export",
  status: "succeeded",
  progress: 1,
  params: {},
  result: { bytes: 2048, rows: { products: 3, sales_records: 60 }, expires_at: "2026-11-01T00:00:00Z" },
  error: null,
  attempts: 1,
  cancel_requested: false,
  created_at: "2026-10-27T10:00:00Z",
  started_at: null,
  finished_at: null,
  heartbeat_at: null,
  ...over,
});

describe("data export helpers", () => {
  it("labels each export and only offers ready, unexpired ones", () => {
    expect(exportState(job({}), NOW)).toEqual({ label: "Ready", downloadable: true });
    expect(exportState(job({ status: "running" }), NOW).label).toBe("Preparing");
    expect(exportState(job({ status: "failed" }), NOW)).toEqual({ label: "Failed", downloadable: false });
    expect(exportState(job({ result: { expired: true } }), NOW).label).toBe("Expired");
    expect(exportState(job({ result: { expires_at: "2026-10-27T11:00:00Z" } }), NOW).downloadable).toBe(false);
    expect(exportRows(job({}))).toBe(63);
    expect(exportRows(job({ result: null }))).toBe(0);
  });

  it("counts days to a deletion, rounding up", () => {
    expect(daysUntil("2026-11-03T12:00:00Z", NOW)).toBe(7);
    expect(daysUntil("2026-10-27T13:00:00Z", NOW)).toBe(1);
    expect(daysUntil("2026-10-26T00:00:00Z", NOW)).toBe(0);
  });
});

const wrap = (ui: React.ReactNode, data: Record<string, unknown>) =>
  render(<SWRConfig value={{ provider: () => new Map(), fetcher: async (k: string) => data[k], dedupingInterval: 0 }}>{ui}</SWRConfig>);

describe("data export card", () => {
  it("lists exports and starts a new one", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(job({ status: "queued" })), { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);
    wrap(<DataExportCard />, { "/data/exports": [job({}), job({ id: "j2", status: "failed", result: null })] });
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Download ZIP" })).toHaveLength(1);
    expect(screen.getByText("63")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Start export" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/data/exports");
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit).method).toBe("POST");
  });
});

describe("organisation deletion card", () => {
  it("only enables scheduling once the exact name and a password are typed", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ scheduled: { due_at: "2026-11-03T12:00:00Z" } }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    wrap(<OrgDeletionCard />, { "/organisation/deletion": { scheduled: null } });
    const button = await screen.findByRole("button", { name: "Schedule deletion in 7 days" });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Organisation name"), { target: { value: "acme" } });
    fireEvent.change(screen.getByLabelText("Your password"), { target: { value: "pw" } });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Organisation name"), { target: { value: "Acme" } });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const sent = JSON.parse((fetchMock.mock.calls[0]?.[1] as RequestInit).body as string);
    expect(sent).toEqual({ confirm_name: "Acme", current_password: "pw" });
  });

  it("shows a scheduled deletion with a way to cancel it", async () => {
    wrap(<OrgDeletionCard />, {
      "/organisation/deletion": { scheduled: { requested_at: "2026-10-27T00:00:00Z", due_at: "2099-01-01T00:00:00Z", requested_by: "boss@example.com" } },
    });
    expect(await screen.findByText(/requested by boss@example.com/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel deletion" })).toBeInTheDocument();
  });
});
