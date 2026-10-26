import { NextRequest, NextResponse } from "next/server";

import { apiUrl, clientAddress, clientAddressHeaders, isSameOriginMutation } from "@/lib/server/auth";

export const dynamic = "force-dynamic";

/** Public: set a new password with an admin-issued one-time link. The body holds the token and the new password, so it
 *  is never logged; the API makes every rule (single use, expiry, password policy, rate limit) authoritative. */
export async function POST(req: NextRequest): Promise<NextResponse> {
  if (!isSameOriginMutation(req)) {
    return NextResponse.json({ error: { code: "csrf", message: "Cross-site request blocked" } }, { status: 403 });
  }
  let payload: { token?: unknown; new_password?: unknown };
  try {
    payload = (await req.json()) as { token?: unknown; new_password?: unknown };
  } catch {
    return NextResponse.json({ error: { code: "bad_request", message: "Invalid JSON" } }, { status: 400 });
  }
  if (typeof payload.token !== "string" || typeof payload.new_password !== "string") {
    return NextResponse.json({ error: { code: "validation_error", message: "The reset link and a new password are required" } }, { status: 422 });
  }
  let upstream: Response;
  try {
    upstream = await fetch(`${apiUrl()}/auth/password-reset`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "user-agent": req.headers.get("user-agent") ?? "aurora-bff",
        ...clientAddressHeaders(clientAddress(req)),
      },
      body: JSON.stringify({ token: payload.token, new_password: payload.new_password }),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ error: { code: "backend_unreachable", message: "The Aurora API is unreachable." } }, { status: 502 });
  }
  if (upstream.ok) return new NextResponse(null, { status: 204 });
  const body = await upstream.json().catch(() => null);
  const retryAfter = upstream.headers.get("retry-after");
  return NextResponse.json(body ?? { error: { code: "error", message: "Password reset failed" } }, {
    status: upstream.status,
    headers: retryAfter ? { "retry-after": retryAfter } : undefined,
  });
}
