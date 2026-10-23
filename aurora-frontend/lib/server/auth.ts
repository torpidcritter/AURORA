import "server-only";

import type { NextRequest, NextResponse } from "next/server";

export const ACCESS_COOKIE = "aurora_at";
export const REFRESH_COOKIE = "aurora_rt";
export const CSRF_HEADER = "x-aurora-csrf";

export function apiUrl(): string {
  const url = process.env.API_URL ?? "http://localhost:8100";
  return url.replace(/\/+$/, "");
}

function secureCookies(): boolean {
  return process.env.NODE_ENV === "production" || process.env.COOKIE_SECURE === "true";
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  refresh_expires_in: number;
}

export function setAuthCookies(res: NextResponse, t: TokenPair): void {
  const base = { httpOnly: true, sameSite: "strict" as const, secure: secureCookies(), path: "/" };
  // Expire the access cookie slightly early so the BFF refreshes before the backend rejects it.
  res.cookies.set(ACCESS_COOKIE, t.access_token, { ...base, maxAge: Math.max(t.expires_in - 30, 30) });
  res.cookies.set(REFRESH_COOKIE, t.refresh_token, { ...base, maxAge: t.refresh_expires_in });
}

export function clearAuthCookies(res: NextResponse): void {
  for (const name of [ACCESS_COOKIE, REFRESH_COOKIE]) {
    res.cookies.set(name, "", { httpOnly: true, sameSite: "strict", secure: secureCookies(), path: "/", maxAge: 0 });
  }
}

/** CSRF defence for state-changing requests: same-origin Origin header plus a custom header that
 *  cross-site forms cannot set. Cookies are also SameSite=Strict. */
export function isSameOriginMutation(req: NextRequest): boolean {
  const origin = req.headers.get("origin");
  const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host");
  if (!origin || !host) return false;
  let originHost: string;
  try {
    originHost = new URL(origin).host;
  } catch {
    return false;
  }
  return originHost === host && req.headers.get(CSRF_HEADER) === "1";
}

/** The client address as seen by the trusted reverse proxy in front of this app.
 *  Proxies append the address they saw, so only the right-most entry is trustworthy; anything to its left can be
 *  forged by the client. Deploy behind a proxy that sets or appends X-Forwarded-For (see docs/RUNBOOK.md). */
export function clientAddress(req: NextRequest): string | null {
  const xff = req.headers.get("x-forwarded-for");
  if (!xff) return null;
  const last = xff.split(",").pop()?.trim() ?? "";
  return /^[0-9a-fA-F:.]{2,45}$/.test(last) ? last : null;
}

const inflight = new Map<string, Promise<RefreshResult>>();

export type RefreshResult =
  | { ok: true; tokens: TokenPair }
  | { ok: false; status: number; code: string | null };

/** Rotate the refresh token. Concurrent refreshes with the same token in this process share one request. */
export function refreshTokens(refreshToken: string, forwardedFor: string | null): Promise<RefreshResult> {
  const existing = inflight.get(refreshToken);
  if (existing) return existing;
  const p = (async (): Promise<RefreshResult> => {
    try {
      const r = await fetch(`${apiUrl()}/auth/refresh`, {
        method: "POST",
        headers: { "content-type": "application/json", ...(forwardedFor ? { "x-forwarded-for": forwardedFor } : {}) },
        body: JSON.stringify({ refresh_token: refreshToken }),
        cache: "no-store",
      });
      if (r.ok) return { ok: true, tokens: (await r.json()) as TokenPair };
      const body = (await r.json().catch(() => null)) as { error?: { code?: string } } | null;
      return { ok: false, status: r.status, code: body?.error?.code ?? null };
    } catch {
      return { ok: false, status: 503, code: "backend_unreachable" };
    }
  })();
  inflight.set(refreshToken, p);
  void p.finally(() => setTimeout(() => inflight.delete(refreshToken), 5_000));
  return p;
}
