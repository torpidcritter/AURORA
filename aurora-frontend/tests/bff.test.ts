import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GET, POST } from "@/app/api/proxy/[...path]/route";
import { isSameOriginMutation, refreshTokens } from "@/lib/server/auth";

const ctx = (...path: string[]) => ({ params: Promise.resolve({ path }) });
const req = (url: string, init: { method?: string; headers?: Record<string, string>; body?: string } = {}) =>
  new NextRequest(new URL(url), init);

describe("isSameOriginMutation", () => {
  it("requires matching Origin and the custom header", () => {
    const ok = req("http://app.test/api/proxy/x", { method: "POST", headers: { origin: "http://app.test", host: "app.test", "x-aurora-csrf": "1" } });
    expect(isSameOriginMutation(ok)).toBe(true);
    const cross = req("http://app.test/api/proxy/x", { method: "POST", headers: { origin: "http://evil.test", host: "app.test", "x-aurora-csrf": "1" } });
    expect(isSameOriginMutation(cross)).toBe(false);
    const noHeader = req("http://app.test/api/proxy/x", { method: "POST", headers: { origin: "http://app.test", host: "app.test" } });
    expect(isSameOriginMutation(noHeader)).toBe(false);
    const noOrigin = req("http://app.test/api/proxy/x", { method: "POST", headers: { host: "app.test", "x-aurora-csrf": "1" } });
    expect(isSameOriginMutation(noOrigin)).toBe(false);
  });
});

describe("proxy route", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("blocks cross-site mutations before contacting the backend", async () => {
    const r = await POST(req("http://app.test/api/proxy/alerts/1/resolve", { method: "POST", headers: { origin: "http://evil.test", host: "app.test" } }), ctx("alerts", "1", "resolve"));
    expect(r.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects odd paths and operational endpoints", async () => {
    expect((await GET(req("http://app.test/api/proxy/x"), ctx("..", "secret"))).status).toBe(400);
    expect((await GET(req("http://app.test/api/proxy/x"), ctx("a%2Fb"))).status).toBe(400);
    expect((await GET(req("http://app.test/api/proxy/metrics"), ctx("metrics"))).status).toBe(404);
    expect((await GET(req("http://app.test/api/proxy/openapi.json"), ctx("openapi.json"))).status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns 401 without cookies and never calls the API anonymously", async () => {
    const r = await GET(req("http://app.test/api/proxy/products"), ctx("products"));
    expect(r.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards the access token as a bearer header and strips it from the browser response", async () => {
    fetchMock.mockResolvedValueOnce(new Response('{"items":[]}', { status: 200, headers: { "content-type": "application/json", "set-cookie": "x=1" } }));
    const r = await GET(req("http://app.test/api/proxy/products?limit=5", { headers: { cookie: "aurora_at=AT1" } }), ctx("products"));
    expect(r.status).toBe(200);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("http://localhost:8100/products?limit=5");
    expect((init.headers as Headers).get("authorization")).toBe("Bearer AT1");
    expect(r.headers.get("set-cookie")).toBeNull();
  });

  it("refreshes once on 401 and sets rotated httpOnly cookies", async () => {
    fetchMock
      .mockResolvedValueOnce(new Response("{}", { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: "AT2", refresh_token: "RT2", expires_in: 1800, refresh_expires_in: 604800 }), { status: 200 }))
      .mockResolvedValueOnce(new Response('{"ok":true}', { status: 200, headers: { "content-type": "application/json" } }));
    const r = await GET(req("http://app.test/api/proxy/auth/me", { headers: { cookie: "aurora_at=OLD; aurora_rt=RT1" } }), ctx("auth", "me"));
    expect(r.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect((fetchMock.mock.calls[2]![1].headers as Headers).get("authorization")).toBe("Bearer AT2");
    const cookies = r.headers.getSetCookie().join("\n");
    expect(cookies).toMatch(/aurora_at=AT2/);
    expect(cookies).toMatch(/aurora_rt=RT2/);
    expect(cookies.toLowerCase()).toMatch(/httponly/);
    expect(cookies.toLowerCase()).toMatch(/samesite=strict/);
  });
});

describe("refreshTokens", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("shares one backend call between concurrent refreshes of the same token", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ access_token: "a", refresh_token: "b", expires_in: 1, refresh_expires_in: 1 }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const [a, b] = await Promise.all([refreshTokens("same-token", null), refreshTokens("same-token", null)]);
    expect(a).toEqual(b);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it("reports an unreachable backend as 503", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    await expect(refreshTokens("other-token", null)).resolves.toEqual({ ok: false, status: 503, code: "backend_unreachable" });
  });
});

describe("clientAddress", () => {
  it("uses only the right-most X-Forwarded-For hop (the one the trusted proxy added)", async () => {
    const { clientAddress } = await import("@/lib/server/auth");
    const mk = (xff?: string) => req("http://app.test/x", { headers: xff ? { "x-forwarded-for": xff } : {} });
    expect(clientAddress(mk("1.2.3.4, 10.0.0.7"))).toBe("10.0.0.7");
    expect(clientAddress(mk("2001:db8::1"))).toBe("2001:db8::1");
    expect(clientAddress(mk("1.2.3.4, not-an-ip<script>"))).toBeNull();
    expect(clientAddress(mk())).toBeNull();
  });
});

describe("clientAddressHeaders", () => {
  it("adds the signed client address only when a shared secret is configured", async () => {
    const { clientAddressHeaders } = await import("@/lib/server/auth");
    vi.stubEnv("BFF_SHARED_SECRET", "");
    expect(clientAddressHeaders("10.0.0.7")).toEqual({ "x-forwarded-for": "10.0.0.7" });
    vi.stubEnv("BFF_SHARED_SECRET", "s3cret-value");
    expect(clientAddressHeaders("10.0.0.7")).toEqual({
      "x-forwarded-for": "10.0.0.7",
      "x-aurora-client-ip": "10.0.0.7",
      "x-aurora-bff": "s3cret-value",
    });
    expect(clientAddressHeaders(null)).toEqual({});
    vi.unstubAllEnvs();
  });
});
