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
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!email.trim() || !password) {
      setError("Enter your email and password.");
      return;
    }
    setBusy(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json", "x-aurora-csrf": "1" },
        body: JSON.stringify({ email: email.trim(), password }),
      });
      if (res.ok) {
        router.replace(safeNext(params.get("next")));
        router.refresh();
        return;
      }
      const body = (await res.json().catch(() => null)) as { error?: { message?: string } } | null;
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
      <Button type="submit" variant="primary" loading={busy} className="w-full">
        Sign in
      </Button>
      <p className="text-center text-xs text-muted">Forgot your password? Ask an administrator for a reset link.</p>
    </form>
  );
}
