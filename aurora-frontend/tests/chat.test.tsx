import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AssistantPage from "@/app/(app)/assistant/page";
import { ChatProvider, chatHistory, type ChatMsg } from "@/components/chat";
import { ChatPanel, isChatPanelHidden } from "@/components/ChatPanel";

const nav = vi.hoisted(() => ({ pathname: "/dashboard" }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

const session = vi.hoisted(() => ({ permissions: ["read"] as string[] }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({ me: undefined, loading: false, can: (p: string) => session.permissions.includes(p) }),
}));

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const answer = { answer: "2 products are critical.", mode: "fallback", tool_calls: [{ tool: "risk_summary", ok: true }], notice: null };

const wrap = (ui: ReactNode) => render(<ChatProvider>{ui}</ChatProvider>);

describe("ChatPanel", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    nav.pathname = "/dashboard";
    session.permissions = ["read"];
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("opens a labelled dialog from a labelled launcher and moves focus into it", async () => {
    wrap(<ChatPanel />);
    const launcher = screen.getByRole("button", { name: "Ask the assistant" });
    expect(launcher).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("dialog")).toBeNull();

    fireEvent.click(launcher);
    const dialog = screen.getByRole("dialog", { name: "Assistant" });
    expect(launcher).toHaveAttribute("aria-expanded", "true");
    expect(launcher).toHaveAttribute("aria-controls", dialog.id);
    await waitFor(() => expect(screen.getByLabelText("Message")).toHaveFocus());
  });

  it("closes on Escape and returns focus to the launcher", async () => {
    wrap(<ChatPanel />);
    const launcher = screen.getByRole("button", { name: "Ask the assistant" });
    fireEvent.click(launcher);
    const input = screen.getByLabelText("Message");
    await waitFor(() => expect(input).toHaveFocus());

    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(launcher).toHaveFocus();
    expect(launcher).toHaveAttribute("aria-expanded", "false");
  });

  it("closes from its close button and returns focus to the launcher", () => {
    wrap(<ChatPanel />);
    const launcher = screen.getByRole("button", { name: "Ask the assistant" });
    fireEvent.click(launcher);
    fireEvent.click(screen.getByRole("button", { name: "Close assistant" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(launcher).toHaveFocus();
  });

  it("sends the message through the BFF proxy with the CSRF header and shows the answer", async () => {
    fetchMock.mockResolvedValueOnce(json(200, answer));
    const user = userEvent.setup();
    wrap(<ChatPanel />);
    await user.click(screen.getByRole("button", { name: "Ask the assistant" }));
    await user.type(screen.getByLabelText("Message"), "Which products are at risk?{Enter}");

    expect(await screen.findByText("2 products are critical.")).toBeInTheDocument();
    expect(screen.getByText("Built-in summary")).toBeInTheDocument();
    expect(screen.getByText(/Data used: risk summary/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/proxy/chat");
    expect(init.method).toBe("POST");
    expect(init.headers["x-aurora-csrf"]).toBe("1");
    expect(JSON.parse(init.body)).toEqual({ message: "Which products are at risk?", history: [] });
    expect(screen.getByLabelText("Message")).toHaveValue("");
  });

  it("sends earlier turns as history on the next question", async () => {
    fetchMock.mockResolvedValueOnce(json(200, answer)).mockResolvedValueOnce(json(200, { ...answer, answer: "Reorder SKU-1." }));
    wrap(<ChatPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Ask the assistant" }));
    fireEvent.click(screen.getByRole("button", { name: "Which products are at risk of stocking out?" }));
    await screen.findByText("2 products are critical.");
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "And what should I reorder?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText("Reorder SKU-1.");
    expect(JSON.parse(fetchMock.mock.calls[1]![1].body)).toEqual({
      message: "And what should I reorder?",
      history: [
        { role: "user", content: "Which products are at risk of stocking out?" },
        { role: "assistant", content: "2 products are critical." },
      ],
    });
  });

  it("shows the API error and keeps the panel usable", async () => {
    fetchMock.mockResolvedValueOnce(json(429, { error: { code: "rate_limited", message: "Too many requests", request_id: "r9" } }));
    wrap(<ChatPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Ask the assistant" }));
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Too many requests")).toBeInTheDocument();
    expect(screen.getByText("Request ID: r9")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Assistant" })).toBeInTheDocument();
  });

  it("does not send an empty or whitespace-only message", async () => {
    wrap(<ChatPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Ask the assistant" }));
    const input = screen.getByLabelText("Message");
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("is not rendered on the assistant page or the /chat redirect", () => {
    nav.pathname = "/assistant";
    const { rerender } = wrap(<ChatPanel />);
    expect(screen.queryByRole("button", { name: "Ask the assistant" })).toBeNull();
    nav.pathname = "/chat";
    rerender(
      <ChatProvider>
        <ChatPanel />
      </ChatProvider>,
    );
    expect(screen.queryByRole("button", { name: "Ask the assistant" })).toBeNull();
    expect(isChatPanelHidden("/assistant")).toBe(true);
    expect(isChatPanelHidden("/assistants")).toBe(false);
    expect(isChatPanelHidden("/products/1")).toBe(false);
  });

  it("is hidden for a session without the read permission (the API refuses /chat anyway)", () => {
    session.permissions = [];
    wrap(<ChatPanel />);
    expect(screen.queryByRole("button", { name: "Ask the assistant" })).toBeNull();
  });

  it("starts empty again in a new provider (the signed-in layout unmounts on sign-out)", async () => {
    fetchMock.mockResolvedValueOnce(json(200, answer));
    const first = wrap(<ChatPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Ask the assistant" }));
    fireEvent.click(screen.getByRole("button", { name: "Summarise open alerts" }));
    await screen.findByText("2 products are critical.");
    first.unmount();
    wrap(<ChatPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Ask the assistant" }));
    expect(screen.queryByText("2 products are critical.")).toBeNull();
    expect(screen.getByRole("button", { name: "Summarise open alerts" })).toBeInTheDocument();
  });
});

describe("assistant page and panel in one provider", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    nav.pathname = "/dashboard";
    session.permissions = ["read"];
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("shows a question asked in the panel on the full page", async () => {
    fetchMock.mockResolvedValueOnce(json(200, answer));
    // Stand-in for AppShell: the provider stays mounted while the page under it changes.
    const Shell = ({ path }: { path: string }) => (
      <>
        {path === "/assistant" && <AssistantPage />}
        <ChatPanel />
      </>
    );
    const { rerender } = render(
      <ChatProvider>
        <Shell path="/dashboard" />
      </ChatProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Ask the assistant" }));
    fireEvent.click(screen.getByRole("button", { name: "Summarise open alerts" }));
    await screen.findByText("2 products are critical.");
    nav.pathname = "/assistant";
    rerender(
      <ChatProvider>
        <Shell path="/assistant" />
      </ChatProvider>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("heading", { name: "Assistant", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("2 products are critical.")).toBeInTheDocument();
    expect(screen.getByText("Summarise open alerts")).toBeInTheDocument();
  });
});

describe("chatHistory", () => {
  it("keeps the last 10 turns and trims each to the API's 8,000-character limit", () => {
    const msgs: ChatMsg[] = Array.from({ length: 14 }, (_, i) => ({ role: i % 2 ? "assistant" : "user", content: `m${i}` }));
    msgs.push({ role: "assistant", content: "x".repeat(9000) });
    const h = chatHistory(msgs);
    expect(h).toHaveLength(10);
    expect(h[0]).toEqual({ role: "assistant", content: "m5" }); // 15 messages: m0..m13 and the long one
    expect(h[9]!.content).toHaveLength(8000);
    expect(h.every((t) => !("meta" in t))).toBe(true);
  });
});
