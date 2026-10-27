"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Button, Field, Input, Notice } from "@/components/ui";

export function safeNext(next: string | null): string {
  // Only allow same-site relative paths to prevent open redirects. Browsers strip tabs/newlines and treat
  // backslashes as slashes, so resolve against a fixed origin rather than relying on string prefixes alone.
  const fallback = "/dashboard";
  if (!next || !next.startsWith("/") || /[\u0000-\u001f\\]/.test(next)) return fallback;
  try {
    const base = "https://aurora.invalid";
    const u = new URL(next, base);
    if (u.origin !== base) return fallback;
    return `${u.pathname}${u.search}${u.hash}`;
  } catch {
    return fallback;
  }
}

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [otp, setOtp] = useState("");
  const [needsCode, setNeedsCode] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!email.trim() || !password) {
      setError("Enter your email and password.");
      return;
    }
    if (needsCode && !otp.trim()) {
      setError("Enter the code from your authenticator app, or a recovery code.");
      return;
    }
    setBusy(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json", "x-aurora-csrf": "1" },
        body: JSON.stringify({ email: email.trim(), password, ...(needsCode ? { otp: otp.trim() } : {}) }),
      });
      if (res.ok) {
        router.replace(safeNext(params.get("next")));
        router.refresh();
        return;
      }
      const body = (await res.json().catch(() => null)) as { error?: { code?: string; message?: string } } | null;
      if (body?.error?.code === "two_factor_required") {
        setNeedsCode(true);
        return;
      }
      if (body?.error?.code === "invalid_two_factor_code") setOtp("");
      if (res.status === 429) setError("Too many attempts. Wait a minute and try again.");
      else setError(body?.error?.message ?? "Sign-in failed.");
    } catch {
      setError("Cannot reach the server. Check your connection.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} noValidate className="glass space-y-4 rounded-xl border border-glass-line p-6 shadow-glass">
      <div>
        <h1 className="text-lg font-semibold">Sign in</h1>
        <p className="mt-1 text-sm text-muted">Use the account your administrator created for you.</p>
      </div>
      {error && <Notice tone="bad">{error}</Notice>}
      <Field label="Email" htmlFor="email">
        <Input id="email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
      </Field>
      <Field label="Password" htmlFor="password">
        <Input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
      </Field>
      {needsCode && (
        <Field label="Authentication code" htmlFor="otp" hint="The 6-digit code from your authenticator app, or one of your recovery codes.">
          <Input id="otp" autoComplete="one-time-code" inputMode="text" autoFocus value={otp} onChange={(e) => setOtp(e.target.value)} required />
        </Field>
      )}
      <Button type="submit" variant="primary" loading={busy} className="w-full">
        Sign in
      </Button>
      <p className="text-center text-xs text-muted">Forgot your password? Ask an administrator for a reset link.</p>
    </form>
  );
}
