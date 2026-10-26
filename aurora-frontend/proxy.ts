import { NextRequest, NextResponse } from "next/server";

const PUBLIC = ["/login", "/reset-password", "/api/", "/_next/", "/favicon", "/robots.txt"];

/** Redirect unauthenticated page requests to /login. This is a UX guard only:
 *  every API call is authorised by the backend. */
export function proxy(req: NextRequest): NextResponse {
  const { pathname, search } = req.nextUrl;
  if (PUBLIC.some((p) => pathname.startsWith(p))) return NextResponse.next();
  const hasSession = req.cookies.has("aurora_rt") || req.cookies.has("aurora_at");
  if (!hasSession) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.search = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname + search)}`;
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
