import { NextRequest, NextResponse } from "next/server";

import {
  ACCESS_COOKIE,
  REFRESH_COOKIE,
  apiUrl,
  clearAuthCookies,
  isSameOriginMutation,
  refreshTokens,
  setAuthCookies,
  type TokenPair,
} from "@/lib/server/auth";

export const dynamic = "force-dynamic";

const MAX_BODY = 25 * 1024 * 1024;
const FORWARD_REQ_HEADERS = ["content-type", "accept", "idempotency-key", "x-request-id"];
const FORWARD_RES_HEADERS = ["content-type", "x-request-id", "retry-after", "content-disposition"];
const SAFE_PATH = /^[A-Za-z0-9\-_./]+$/;
// Operational endpoints are for the internal network / Prometheus only, never for browsers.
const BLOCKED_PATH = /^(metrics|docs|redoc|openapi\.json)(\/|$)/;

function errorResponse(status: number, code: string, message: string): NextResponse {
  return NextResponse.json({ error: { code, message, details: null, request_id: null } }, { status });
}

async function handle(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }): Promise<NextResponse> {
  const { path } = await ctx.params;
  const joined = path.join("/");
  if (!SAFE_PATH.test(joined) || joined.includes("..")) return errorResponse(400, "bad_path", "Invalid API path");
  if (BLOCKED_PATH.test(joined)) return errorResponse(404, "not_found", "Not found");

  const method = req.method.toUpperCase();
  if (method !== "GET" && method !== "HEAD" && !isSameOriginMutation(req)) {
    return errorResponse(403, "csrf", "Cross-site request blocked");
  }

  let body: ArrayBuffer | undefined;
  if (method !== "GET" && method !== "HEAD") {
    const len = Number(req.headers.get("content-length") ?? "0");
    if (len > MAX_BODY) return errorResponse(413, "too_large", "Request body too large");
    body = await req.arrayBuffer();
    if (body.byteLength > MAX_BODY) return errorResponse(413, "too_large", "Request body too large");
  }

  const target = `${apiUrl()}/${joined}${req.nextUrl.search}`;
  const forwardedFor = req.headers.get("x-forwarded-for");
  let access = req.cookies.get(ACCESS_COOKIE)?.value ?? null;
  const refresh = req.cookies.get(REFRESH_COOKIE)?.value ?? null;
  let rotated: TokenPair | null = null;

  const doRefresh = async (): Promise<NextResponse | null> => {
    if (!refresh) return errorResponse(401, "unauthorized", "Not signed in");
    const r = await refreshTokens(refresh, forwardedFor);
    if (r.ok) {
      rotated = r.tokens;
      access = r.tokens.access_token;
      return null;
    }
    const res = errorResponse(r.status === 503 ? 503 : 401, r.code ?? "unauthorized", "Session expired");
    // A concurrent request already rotated the token: keep cookies, the client retries once.
    if (r.code !== "refresh_superseded" && r.status !== 503) clearAuthCookies(res);
    return res;
  };

  if (!access) {
    const failed = await doRefresh();
    if (failed) return failed;
  }

  const send = () => {
    const headers = new Headers();
    for (const h of FORWARD_REQ_HEADERS) {
      const v = req.headers.get(h);
      if (v) headers.set(h, v);
    }
    headers.set("authorization", `Bearer ${access}`);
    if (forwardedFor) headers.set("x-forwarded-for", forwardedFor);
    return fetch(target, { method, headers, body, cache: "no-store", redirect: "manual" });
  };

  let upstream: Response;
  try {
    upstream = await send();
    if (upstream.status === 401 && !rotated) {
      const failed = await doRefresh();
      if (failed) return failed;
      upstream = await send();
    }
  } catch {
    return errorResponse(502, "backend_unreachable", "The Aurora API is unreachable. Try again shortly.");
  }

  const resHeaders = new Headers();
  for (const h of FORWARD_RES_HEADERS) {
    const v = upstream.headers.get(h);
    if (v) resHeaders.set(h, v);
  }
  const res = new NextResponse(upstream.status === 204 ? null : await upstream.arrayBuffer(), {
    status: upstream.status,
    headers: resHeaders,
  });
  if (rotated) setAuthCookies(res, rotated);
  return res;
}

export { handle as GET, handle as POST, handle as PATCH, handle as PUT, handle as DELETE };
