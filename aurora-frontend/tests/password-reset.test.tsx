import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "@/app/api/auth/password-reset/route";
import { ResetPasswordForm, takeResetToken } from "@/app/reset-password/ResetPasswordForm";
import { ResetLinkPanel } from "@/components/ResetLinkPanel";
import { proxy } from "@/proxy";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const TOKEN = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-abcd";
const SAME_ORIGIN = { origin: "http://app.test", host: "app.test", "x-aurora-csrf": "1", "content-type": "application/json" };
const req = (headers: Record<string, string>, body: string) =>
  new NextRequest(new URL("http://app.test/api/auth/password-reset"), { method: "POST", headers, body });
const goodBody = JSON.stringify({ token: TOKEN, new_password: "Fresh-Start-pw-2026" });

describe("password-reset BFF route", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("refuses cross-site requests before contacting the backend", async () => {
    const cross = await POST(req({ ...SAME_ORIGIN, origin: "http://evil.test" }, goodBody));
    expect(cross.status).toBe(403);
    expect((await cross.json()).error.code).toBe("csrf");
    const { "x-aurora-csrf": _omit, ...noHeader } = SAME_ORIGIN;
    void _omit;
    expect((await POST(req(noHeader, goodBody))).status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects malformed bodies without calling the backend", async () => {
    expect((await POST(req(SAME_ORIGIN, "{not json"))).status).toBe(400);
    expect((await POST(req(SAME_ORIGIN, JSON.stringify({ token: TOKEN })))).status).toBe(422);
    expect((await POST(req(SAME_ORIGIN, JSON.stringify({ token: 1, new_password: "x" })))).status).toBe(422);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards only the token, the password and the client address, and sets no cookies", async () => {
    vi.stubEnv("BFF_SHARED_SECRET", "s3cret-value");
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    const extra = JSON.stringify({ token: TOKEN, new_password: "Fresh-Start-pw-2026", role: "admin" });
    const r = await POST(req({ ...SAME_ORIGIN, "x-forwarded-for": "1.2.3.4, 10.0.0.7", cookie: "aurora_at=AT" }, extra));
    expect(r.status).toBe(204);
    expect(r.headers.get("set-cookie")).toBeNull();
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("http://localhost:8100/auth/password-reset");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ token: TOKEN, new_password: "Fresh-Start-pw-2026" });
    const headers = init.headers as Record<string, string>;
    expect(headers["x-forwarded-for"]).toBe("10.0.0.7");
    expect(headers["x-aurora-client-ip"]).toBe("10.0.0.7");
    expect(headers["x-aurora-bff"]).toBe("s3cret-value");
    expect(headers.authorization).toBeUndefined();
    expect(headers.cookie).toBeUndefined();
  });

  it("passes API errors and Retry-After through, and never logs the body", async () => {
    const logs = (["log", "info", "warn", "error", "debug"] as const).map((m) => vi.spyOn(console, m).mockImplementation(() => undefined));
    const invalid = { error: { code: "invalid_reset_token", message: "This reset link is invalid", details: null } };
    fetchMock
      .mockResolvedValueOnce(new Response(JSON.stringify(invalid), { status: 400, headers: { "content-type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: "rate_limited", message: "Too many requests" } }), { status: 429, headers: { "retry-after": "17" } }));
    const r1 = await POST(req(SAME_ORIGIN, goodBody));
    expect(r1.status).toBe(400);
    expect(await r1.json()).toEqual(invalid);
    const r2 = await POST(req(SAME_ORIGIN, goodBody));
    expect(r2.status).toBe(429);
    expect(r2.headers.get("retry-after")).toBe("17");
    for (const spy of logs) {
      for (const call of spy.mock.calls) expect(JSON.stringify(call)).not.toContain(TOKEN);
    }
  });

  it("reports an unreachable backend as 502", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("fetch failed"));
    expect((await POST(req(SAME_ORIGIN, goodBody))).status).toBe(502);
  });
});

describe("proxy (page guard)", () => {
  it("lets signed-out visitors open the reset page but not the app", () => {
    const open = proxy(new NextRequest(new URL("http://app.test/reset-password")));
    expect(open.headers.get("location")).toBeNull();
    const guarded = proxy(new NextRequest(new URL("http://app.test/dashboard")));
    expect(guarded.headers.get("location")).toMatch(/\/login\?next=%2Fdashboard$/);
  });
});

describe("takeResetToken", () => {
  const loc = (hash: string) => ({ hash, pathname: "/reset-password", search: "" });
  it("reads the token from the fragment and removes it from the address bar", () => {
    const hist = { replaceState: vi.fn() };
    expect(takeResetToken(loc(`#token=${TOKEN}`), hist)).toBe(TOKEN);
    expect(hist.replaceState).toHaveBeenCalledWith(null, "", "/reset-password");
  });
  it("rejects missing or malformed tokens (and still clears the fragment)", () => {
    const hist = { replaceState: vi.fn() };
    expect(takeResetToken(loc(""), hist)).toBeNull();
    expect(hist.replaceState).not.toHaveBeenCalled();
    expect(takeResetToken(loc("#token=short"), hist)).toBeNull();
    expect(takeResetToken(loc(`#token=${TOKEN}<script>`), hist)).toBeNull();
    expect(takeResetToken(loc("#other=1"), hist)).toBeNull();
    expect(hist.replaceState).toHaveBeenCalledTimes(3);
  });
});

describe("ResetPasswordForm", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    window.history.replaceState(null, "", `/reset-password#token=${TOKEN}`);
  });
  afterEach(() => vi.unstubAllGlobals());

  const fill = (a: string, b: string) => {
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: a } });
    fireEvent.change(screen.getByLabelText("Repeat the new password"), { target: { value: b } });
    fireEvent.click(screen.getByRole("button", { name: "Set new password" }));
  };

  it("clears the token from the address bar and validates before posting", async () => {
    render(<ResetPasswordForm />);
    await screen.findByLabelText("New password");
    expect(window.location.hash).toBe("");
    expect(window.location.pathname).toBe("/reset-password");
    fill("weakpassword", "weakpassword");
    expect(await screen.findByText(/at least three of/)).toBeInTheDocument();
    fill("Fresh-Start-pw-2026", "Fresh-Start-pw-2027");
    expect(await screen.findByText(/do not match/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("posts the token and new password to the BFF and confirms success", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    render(<ResetPasswordForm />);
    await screen.findByLabelText("New password");
    fill("Fresh-Start-pw-2026", "Fresh-Start-pw-2026");
    expect(await screen.findByText(/Your password was changed/)).toBeInTheDocument();
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/auth/password-reset");
    expect(init.headers["x-aurora-csrf"]).toBe("1");
    expect(JSON.parse(init.body)).toEqual({ token: TOKEN, new_password: "Fresh-Start-pw-2026" });
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });

  it("explains a used or expired link and drops the form", async () => {
    const body = { error: { code: "invalid_reset_token", message: "This reset link is invalid, has expired or was already used." } };
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(body), { status: 400 }));
    render(<ResetPasswordForm />);
    await screen.findByLabelText("New password");
    fill("Fresh-Start-pw-2026", "Fresh-Start-pw-2026");
    expect(await screen.findByText(/has expired or was already used/)).toBeInTheDocument();
    expect(screen.queryByLabelText("New password")).toBeNull();
  });

  it("asks for a link when the page is opened without one", async () => {
    window.history.replaceState(null, "", "/reset-password");
    render(<ResetPasswordForm />);
    expect(await screen.findByText(/Open this page with the reset link/)).toBeInTheDocument();
    expect(screen.queryByLabelText("New password")).toBeNull();
  });
});

describe("ResetLinkPanel", () => {
  afterEach(() => vi.unstubAllGlobals());
  const link = { reset_url: `http://app.test/reset-password#token=${TOKEN}`, expires_at: "2026-10-26T07:00:00Z" };

  it("shows the link once with the expiry, a copy button and a trusted-channel warning", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    const onDone = vi.fn();
    render(<ResetLinkPanel email="v@example.com" link={link} onDone={onDone} />);
    expect(screen.getByLabelText("Reset link")).toHaveValue(link.reset_url);
    expect(screen.getByText(/trusted channel/)).toBeInTheDocument();
    expect(screen.getByText(/Works once and expires/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Copy link/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(link.reset_url));
    expect(await screen.findByRole("button", { name: /Copied/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(onDone).toHaveBeenCalledOnce();
  });

  it("falls back to selecting the link when the clipboard is unavailable", async () => {
    vi.stubGlobal("navigator", { ...navigator, clipboard: undefined });
    render(<ResetLinkPanel email="v@example.com" link={link} onDone={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: /Copy link/ }));
    expect(await screen.findByText(/Copying is blocked here/)).toBeInTheDocument();
  });
});

describe("LoginForm", () => {
  it("tells users how to recover a forgotten password", async () => {
    const { LoginForm } = await import("@/app/login/LoginForm");
    render(<LoginForm />);
    expect(screen.getByText("Forgot your password? Ask an administrator for a reset link.")).toBeInTheDocument();
  });
});
