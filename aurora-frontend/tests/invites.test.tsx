import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AcceptInviteForm } from "@/app/accept-invite/AcceptInviteForm";
import { GettingStarted } from "@/components/GettingStarted";
import type { Onboarding } from "@/lib/types";

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

describe("accepting an invitation", () => {
  it("reads the token from the fragment, shows who it is for and creates the account", async () => {
    window.history.replaceState(null, "", "/accept-invite#token=abcdefghijklmnopqrstuvwxyz012345");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({ email: "ana@example.com", role: "operator", org_name: "Acme", expires_at: "2030-01-01T00:00:00Z" }))
      .mockResolvedValueOnce(json({ email: "ana@example.com" }, 201));
    vi.stubGlobal("fetch", fetchMock);
    render(<AcceptInviteForm />);
    expect(await screen.findByText("Acme")).toBeInTheDocument();
    expect(window.location.hash).toBe(""); // the token is removed from the address bar
    fireEvent.change(screen.getByLabelText("Your name"), { target: { value: "Ana" } });
    fireEvent.change(screen.getByLabelText("Choose a password"), { target: { value: "Strong-pass-123" } });
    fireEvent.change(screen.getByLabelText("Repeat the password"), { target: { value: "Strong-pass-12" } });
    fireEvent.click(screen.getByRole("button", { name: "Create my account" }));
    expect(await screen.findByText(/do not match/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Repeat the password"), { target: { value: "Strong-pass-123" } });
    fireEvent.click(screen.getByRole("button", { name: "Create my account" }));
    expect(await screen.findByText(/ana@example.com is ready/)).toBeInTheDocument();
    const sent = JSON.parse((fetchMock.mock.calls[1]?.[1] as RequestInit).body as string);
    expect(sent).toEqual({ action: "accept", token: "abcdefghijklmnopqrstuvwxyz012345", full_name: "Ana", password: "Strong-pass-123" });
  });

  it("explains a used or expired link", async () => {
    window.history.replaceState(null, "", "/accept-invite#token=abcdefghijklmnopqrstuvwxyz012345");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ error: { code: "invalid_invitation", message: "This invitation link is invalid, already used or expired." } }, 400)));
    render(<AcceptInviteForm />);
    expect(await screen.findByText(/already used or expired/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create my account" })).toBeNull();
  });
});

const onboarding = (done: boolean[]): Onboarding => ({
  steps: done.map((d, i) => ({ key: `s${i}`, title: `Step ${i}`, detail: `Detail ${i}`, href: `/s${i}`, done: d })),
  done: done.filter(Boolean).length,
  total: done.length,
  complete: done.every(Boolean),
});

function renderChecklist(o: Onboarding) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), fetcher: async () => o, dedupingInterval: 0 }}>
      <GettingStarted />
    </SWRConfig>,
  );
}

describe("getting started checklist", () => {
  it("links open steps and can be hidden", async () => {
    renderChecklist(onboarding([true, false]));
    expect(await screen.findByText("1 of 2 done")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Step 1" })).toHaveAttribute("href", "/s1");
    expect(screen.queryByRole("link", { name: "Step 0" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Hide" }));
    await waitFor(() => expect(screen.queryByText("Getting started")).toBeNull());
    expect(window.localStorage.getItem("aurora.onboarding.dismissed")).toBe("1");
  });

  it("is not shown once everything is done", async () => {
    renderChecklist(onboarding([true, true]));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("Getting started")).toBeNull();
  });
});
