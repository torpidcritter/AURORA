import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, errorMessage, setUnauthorizedHandler } from "@/lib/api";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

describe("api client", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    setUnauthorizedHandler(null);
  });

  it("calls the same-origin proxy and sends the CSRF header only on mutations", async () => {
    fetchMock.mockResolvedValueOnce(json(200, { ok: 1 })).mockResolvedValueOnce(json(200, { ok: 2 }));
    await api("/products");
    await api("/alerts/1/resolve", { method: "POST", body: { a: 1 } });
    const [url1, init1] = fetchMock.mock.calls[0]!;
    const [url2, init2] = fetchMock.mock.calls[1]!;
    expect(url1).toBe("/api/proxy/products");
    expect(init1.headers["x-aurora-csrf"]).toBeUndefined();
    expect(url2).toBe("/api/proxy/alerts/1/resolve");
    expect(init2.headers["x-aurora-csrf"]).toBe("1");
    expect(init2.headers["content-type"]).toBe("application/json");
    expect(init2.body).toBe('{"a":1}');
  });

  it("parses the error envelope", async () => {
    fetchMock.mockResolvedValueOnce(json(409, { error: { code: "conflict", message: "Changed", request_id: "r1" } }));
    const err = await api("/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 409, code: "conflict", message: "Changed", requestId: "r1" });
  });

  it("uses a generic message for 5xx without a body", async () => {
    fetchMock.mockResolvedValueOnce(new Response("oops", { status: 500 }));
    await expect(api("/x")).rejects.toMatchObject({ status: 500, message: expect.stringMatching(/server had a problem/) });
  });

  it("retries once when a concurrent refresh superseded the token", async () => {
    fetchMock
      .mockResolvedValueOnce(json(401, { error: { code: "refresh_superseded", message: "x" } }))
      .mockResolvedValueOnce(json(200, { ok: true }));
    await expect(api("/x")).resolves.toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("calls the unauthorized handler on other 401s", async () => {
    const onUnauth = vi.fn();
    setUnauthorizedHandler(onUnauth);
    fetchMock.mockResolvedValueOnce(json(401, { error: { code: "token_expired", message: "x" } }));
    await expect(api("/x")).rejects.toBeInstanceOf(ApiError);
    expect(onUnauth).toHaveBeenCalledOnce();
  });

  it("returns undefined for 204", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(api("/x", { method: "DELETE" })).resolves.toBeUndefined();
  });
});

describe("errorMessage", () => {
  it("names the first invalid field", () => {
    const e = new ApiError(422, "validation_error", "Invalid", [{ loc: ["body", "horizon"], msg: "too big" }]);
    expect(errorMessage(e)).toBe("horizon: too big");
  });
  it("handles plain errors and unknowns", () => {
    expect(errorMessage(new Error("boom"))).toBe("boom");
    expect(errorMessage(42)).toBe("Something went wrong");
  });
});
