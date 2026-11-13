import { NextRequest, NextResponse } from "next/server";

import { apiUrl, clientAddress, clientAddressHeaders, isSameOriginMutation } from "@/lib/server/auth";

export const dynamic = "force-dynamic";

/** Public: look at or accept a team invitation. The token and password travel in the body only, never in a URL, and the
 *  API makes every rule (single use, expiry, password policy, rate limit) authoritative. */
export async function POST(req: NextRequest): Promise<NextResponse> {
  if (!isSameOriginMutation(req)) {
    return NextResponse.json({ error: { code: "csrf", message: "Cross-site request blocked" } }, { status: 403 });
  }
  let payload: { action?: unknown; token?: unknown; full_name?: unknown; password?: unknown };
  try {
    payload = (await req.json()) as typeof payload;
  } catch {
    return NextResponse.json({ error: { code: "bad_request", message: "Invalid JSON" } }, { status: 400 });
  }
  const action = payload.action === "accept" ? "accept" : payload.action === "inspect" ? "inspect" : null;
  if (!action || typeof payload.token !== "string") {
    return NextResponse.json({ error: { code: "validation_error", message: "An invitation link is required" } }, { status: 422 });
  }
  const body: Record<string, string> = { token: payload.token };
  if (action === "accept") {
    if (typeof payload.password !== "string") {
      return NextResponse.json({ error: { code: "validation_error", message: "A password is required" } }, { status: 422 });
    }
    body.password = payload.password;
    body.full_name = typeof payload.full_name === "string" ? payload.full_name : "";
  }
  let upstream: Response;
  try {
    upstream = await fetch(`${apiUrl()}/auth/invite/${action}`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "user-agent": req.headers.get("user-agent") ?? "aurora-bff",
        ...clientAddressHeaders(clientAddress(req)),
      },
      body: JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ error: { code: "backend_unreachable", message: "The Aurora API is unreachable." } }, { status: 502 });
  }
  const out = await upstream.json().catch(() => null);
  return NextResponse.json(out ?? { error: { code: "error", message: "The invitation could not be processed" } }, { status: upstream.status });
}
