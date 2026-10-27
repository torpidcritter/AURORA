import { NextRequest, NextResponse } from "next/server";

import {
  REFRESH_COOKIE,
  apiUrl,
  clearAuthCookies,
  clientAddress,
  clientAddressHeaders,
  isSameOriginMutation,
} from "@/lib/server/auth";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<NextResponse> {
  if (!isSameOriginMutation(req)) {
    return NextResponse.json({ error: { code: "csrf", message: "Cross-site request blocked" } }, { status: 403 });
  }
  const rt = req.cookies.get(REFRESH_COOKIE)?.value;
  if (rt) {
    await fetch(`${apiUrl()}/auth/logout`, {
      method: "POST",
      headers: { "content-type": "application/json", ...clientAddressHeaders(clientAddress(req)) },
      body: JSON.stringify({ refresh_token: rt }),
      cache: "no-store",
    }).catch(() => undefined); // best effort: cookies are cleared regardless
  }
  const res = new NextResponse(null, { status: 204 });
  clearAuthCookies(res);
  return res;
}
