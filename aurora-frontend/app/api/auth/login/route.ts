import { NextRequest, NextResponse } from "next/server";

import {
  apiUrl,
  clientAddress,
  clientAddressHeaders,
  isSameOriginMutation,
  setAuthCookies,
  type TokenPair,
} from "@/lib/server/auth";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<NextResponse> {
  if (!isSameOriginMutation(req)) {
    return NextResponse.json({ error: { code: "csrf", message: "Cross-site request blocked" } }, { status: 403 });
  }
  let payload: { email?: unknown; password?: unknown };
  try {
    payload = (await req.json()) as { email?: unknown; password?: unknown };
  } catch {
    return NextResponse.json({ error: { code: "bad_request", message: "Invalid JSON" } }, { status: 400 });
  }
  if (typeof payload.email !== "string" || typeof payload.password !== "string") {
    return NextResponse.json({ error: { code: "validation_error", message: "Email and password are required" } }, { status: 422 });
  }
  const fwd = clientAddress(req);
  let upstream: Response;
  try {
    upstream = await fetch(`${apiUrl()}/auth/login`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "user-agent": req.headers.get("user-agent") ?? "aurora-bff",
        ...clientAddressHeaders(fwd),
      },
      body: JSON.stringify({ email: payload.email, password: payload.password }),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ error: { code: "backend_unreachable", message: "The Aurora API is unreachable." } }, { status: 502 });
  }
  const body = await upstream.json().catch(() => null);
  if (!upstream.ok) return NextResponse.json(body ?? { error: { code: "error", message: "Login failed" } }, { status: upstream.status });
  const tokens = body as TokenPair;
  const res = NextResponse.json({ ok: true });
  setAuthCookies(res, tokens);
  return res;
}
