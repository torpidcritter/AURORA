import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SavedViews } from "@/components/SavedViews";
import type { SavedView } from "@/lib/types";

const session = vi.hoisted(() => ({ perms: [] as string[] }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({ me: undefined, loading: false, can: (p: string) => session.perms.includes(p) }),
}));

afterEach(() => vi.unstubAllGlobals());

const view = (over: Partial<SavedView> = {}): SavedView => ({
  id: "v1",
  page: "alerts",
  name: "Critical only",
  params: { severity: "critical" },
  shared: true,
  mine: false,
  created_at: "2099-01-01T00:00:00Z",
  ...over,
});

const wrap = (ui: React.ReactNode, data: Record<string, unknown>) =>
  render(<SWRConfig value={{ provider: () => new Map(), fetcher: async (k: string) => data[k], dedupingInterval: 0 }}>{ui}</SWRConfig>);

describe("SavedViews", () => {
  it("applies a chosen view and only offers delete to its owner or an admin", async () => {
    const onApply = vi.fn();
    wrap(<SavedViews page="alerts" current={{ status: "active", severity: "" }} onApply={onApply} />, { "/saved-views?page=alerts": [view()] });
    const select = await screen.findByLabelText("Saved views");
    await waitFor(() => expect(select).toBeEnabled());
    expect(screen.getByRole("option", { name: "Critical only (shared with you)" })).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "v1" } });
    expect(onApply).toHaveBeenCalledWith({ severity: "critical" });
    expect(screen.queryByRole("button", { name: "Delete view" })).toBeNull();
  });

  it("saves the current filters without empty values", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(view({ id: "v2", mine: true, shared: false })), { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);
    wrap(<SavedViews page="alerts" current={{ status: "active", severity: "" }} onApply={vi.fn()} />, { "/saved-views?page=alerts": [] });
    fireEvent.click(await screen.findByRole("button", { name: "Save these filters" }));
    fireEvent.click(screen.getByRole("button", { name: "Save view" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Give the view a name");
    fireEvent.change(screen.getByLabelText("View name"), { target: { value: " Active " } });
    fireEvent.click(screen.getByLabelText("Share with my organisation"));
    fireEvent.click(screen.getByRole("button", { name: "Save view" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const sent = JSON.parse((fetchMock.mock.calls[0]?.[1] as RequestInit).body as string);
    expect(sent).toEqual({ page: "alerts", name: "Active", params: { status: "active" }, shared: true });
  });
});
