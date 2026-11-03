import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiKeysCard, WebhooksCard } from "@/components/Developers";
import { keyState, rolesFor, webhookUrlProblem } from "@/lib/developers";
import type { ApiKey } from "@/lib/types";

const session = vi.hoisted(() => ({ role: "admin" }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({ me: { role: session.role }, loading: false, can: () => true }),
}));

afterEach(() => vi.unstubAllGlobals());

const key = (over: Partial<ApiKey> = {}): ApiKey => ({
  id: "k1",
  name: "ERP sync",
  prefix: "aur_ab12cd34",
  role: "viewer",
  created_at: "2026-10-01T00:00:00Z",
  created_by_email: "boss@example.com",
  last_used_at: null,
  expires_at: null,
  revoked_at: null,
  active: true,
  ...over,
});

describe("API key helpers", () => {
  it("never offers a key role above the person's own, nor admin", () => {
    expect(rolesFor("admin")).toEqual(["viewer", "operator", "analyst"]);
    expect(rolesFor("operator")).toEqual(["viewer", "operator"]);
    expect(rolesFor("viewer")).toEqual(["viewer"]);
    expect(rolesFor(undefined)).toEqual([]);
  });

  it("labels keys", () => {
    const now = new Date("2026-10-27T00:00:00Z");
    expect(keyState(key(), now)).toBe("active");
    expect(keyState(key({ revoked_at: "2026-10-02T00:00:00Z" }), now)).toBe("revoked");
    expect(keyState(key({ expires_at: "2026-10-26T00:00:00Z" }), now)).toBe("expired");
  });

  it("checks webhook addresses before sending", () => {
    expect(webhookUrlProblem("")).toMatch(/Enter/);
    expect(webhookUrlProblem("not a url")).toMatch(/not a web address/);
    expect(webhookUrlProblem("http://example.com/x")).toMatch(/https/);
    expect(webhookUrlProblem("https://u:p@example.com/x")).toMatch(/user name/);
    expect(webhookUrlProblem("https://example.com/hook")).toBeNull();
  });
});

const wrap = (ui: React.ReactNode, data: Record<string, unknown>) =>
  render(<SWRConfig value={{ provider: () => new Map(), fetcher: async (k: string) => data[k], dedupingInterval: 0 }}>{ui}</SWRConfig>);

describe("API keys card", () => {
  it("creates a key and shows the secret once", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ key: key({ name: "Nightly" }), secret: "aur_ab12cd34_secretvalue" }), { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);
    wrap(<ApiKeysCard />, { "/api-keys": [key({ revoked_at: "2026-10-02T00:00:00Z" })] });
    expect(await screen.findByText("revoked")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Revoke" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Key name"), { target: { value: " Nightly " } });
    fireEvent.change(screen.getByLabelText("Key role"), { target: { value: "analyst" } });
    fireEvent.change(screen.getByLabelText("Expires after"), { target: { value: "never" } });
    fireEvent.click(screen.getByRole("button", { name: "Create key" }));
    expect(await screen.findByDisplayValue("aur_ab12cd34_secretvalue")).toBeInTheDocument();
    const sent = JSON.parse((fetchMock.mock.calls[0]?.[1] as RequestInit).body as string);
    expect(sent).toEqual({ name: "Nightly", role: "analyst", expires_in_days: null });
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByDisplayValue("aur_ab12cd34_secretvalue")).toBeNull();
  });

  it("offers an operator only viewer and operator keys", async () => {
    session.role = "operator";
    wrap(<ApiKeysCard />, { "/api-keys": [] });
    const options = Array.from((await screen.findByLabelText("Key role")).querySelectorAll("option")).map((o) => o.getAttribute("value"));
    expect(options).toEqual(["viewer", "operator"]);
    session.role = "admin";
  });
});

describe("webhooks card", () => {
  const data = {
    "/integrations/webhooks/events": [
      { key: "alert.opened", description: "A new alert" },
      { key: "purchase_order.status_changed", description: "An order moved" },
    ],
    "/integrations/webhooks": [],
    "/integrations/webhooks/deliveries?limit=20": { items: [], total: 0, limit: 20, offset: 0 },
  };

  it("refuses a plain http address without calling the API", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    wrap(<WebhooksCard />, data);
    fireEvent.change(await screen.findByLabelText("Endpoint URL"), { target: { value: "http://example.com/x" } });
    fireEvent.click(screen.getByRole("button", { name: "Add endpoint" }));
    expect(await screen.findByText(/must start with https/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("adds an endpoint for the chosen events and shows its signing secret", async () => {
    const endpoint = { id: "e1", url_hint: "https://example.com/…", description: "", events: ["alert.opened"], active: true };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ endpoint, secret: "whsec_abc" }), { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);
    wrap(<WebhooksCard />, data);
    fireEvent.change(await screen.findByLabelText("Endpoint URL"), { target: { value: "https://example.com/hook" } });
    fireEvent.click(await screen.findByLabelText(/alert\.opened/));
    fireEvent.click(screen.getByRole("button", { name: "Add endpoint" }));
    expect(await screen.findByDisplayValue("whsec_abc")).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const sent = JSON.parse((fetchMock.mock.calls[0]?.[1] as RequestInit).body as string);
    expect(sent).toEqual({ url: "https://example.com/hook", description: "", events: ["alert.opened"] });
  });
});
