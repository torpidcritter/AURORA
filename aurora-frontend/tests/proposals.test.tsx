import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ProposalCard } from "@/components/chat";
import { proposalOutcome } from "@/lib/proposals";
import type { AssistantProposal } from "@/lib/types";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

const base: AssistantProposal = {
  id: "p1",
  kind: "draft_purchase_orders",
  summary: "Draft purchase orders (one per supplier) for TEA-1 (40), using these quantities. Nothing is approved or sent.",
  status: "pending",
  result: null,
  created_at: "2099-01-01T00:00:00Z",
  expires_at: "2099-01-02T00:00:00Z",
};

describe("proposal outcome", () => {
  it("describes each result in plain words", () => {
    const drafted = proposalOutcome({ ...base, status: "applied", result: { orders: [{ id: "o1", number: "PO-000007" }], skipped: { already_on_order: 1 } } });
    expect(drafted.text).toMatch(/^Drafted 1 purchase order; 1 product\(s\) skipped/);
    expect(drafted.links).toEqual([{ href: "/purchase-orders/o1", label: "PO-000007" }]);
    expect(proposalOutcome({ ...base, status: "applied", result: { orders: [], skipped: {} } }).text).toMatch(/No order was drafted/);
    expect(proposalOutcome({ ...base, kind: "acknowledge_alerts", status: "applied", result: { acknowledged: 2, skipped: 1 } }).text).toBe(
      "Acknowledged 2 alerts; 1 had already been closed.",
    );
    expect(proposalOutcome({ ...base, kind: "add_forecast_override", status: "applied", result: { product_id: "x" } }).links[0]?.href).toBe("/products/x");
    expect(proposalOutcome({ ...base, status: "failed", result: { error: "Overlaps" } }).text).toBe("Not done: Overlaps. Nothing was changed.");
    expect(proposalOutcome({ ...base, status: "dismissed" }).text).toMatch(/Nothing was changed/);
  });
});

describe("ProposalCard", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("changes nothing until confirmed, then shows the result", async () => {
    render(<ProposalCard proposal={base} />);
    expect(screen.getByRole("group", { name: "Proposed change" })).toHaveTextContent("needs your confirmation");
    expect(fetchMock).not.toHaveBeenCalled();
    fetchMock.mockResolvedValueOnce(json(200, { ...base, status: "applied", result: { orders: [{ id: "o1", number: "PO-000007" }], skipped: {} } }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm change" }));
    await waitFor(() => expect(screen.getByRole("link", { name: "PO-000007" })).toHaveAttribute("href", "/purchase-orders/o1"));
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/assistant/proposals/p1/apply");
    expect(screen.queryByRole("button", { name: "Confirm change" })).toBeNull();
  });

  it("shows why a decision failed and keeps the buttons", async () => {
    render(<ProposalCard proposal={base} />);
    fetchMock.mockResolvedValueOnce(json(409, { error: { code: "proposal_closed", message: "This proposal is expired" } }));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss proposal" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("This proposal is expired"));
    expect(screen.getByRole("button", { name: "Confirm change" })).toBeEnabled();
  });
});
