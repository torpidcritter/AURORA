"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { Button, Field, Input, Notice } from "@/components/ui";
import { passwordProblem } from "@/lib/validation";

/** Read the one-time token from the URL fragment (`#token=...`) and remove it from the address bar and history, so it
 *  is not left behind in the browser history, bookmarks or a screenshot. Fragments never reach a server. */
export function takeResetToken(loc: Pick<Location, "hash" | "pathname" | "search">, hist: Pick<History, "replaceState">): string | null {
  if (!loc.hash) return null;
  const token = new URLSearchParams(loc.hash.replace(/^#/, "")).get("token");
  hist.replaceState(null, "", loc.pathname + loc.search);
  return token && /^[A-Za-z0-9_-]{20,200}$/.test(token) ? token : null;
}

type State = { phase: "reading" } | { phase: "missing" } | { phase: "ready"; token: string } | { phase: "done" };

export function ResetPasswordForm() {
  const [state, setState] = useState<State>({ phase: "reading" });
  const [pw, setPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // The token is taken from the address bar exactly once; StrictMode runs effects twice, the ref survives that.
  const taken = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    // The fragment only exists in the browser (never in the server render), so it is read once after mount.
    if (taken.current === undefined) taken.current = takeResetToken(window.location, window.history);
    const token = taken.current;
    setState(token ? { phase: "ready", token } : { phase: "missing" });
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (state.phase !== "ready") return;
    setError(null);
    const problem = passwordProblem(pw);
    if (problem) return setError(`${problem}.`);
    if (pw !== confirm) return setError("The two passwords do not match.");
    setBusy(true);
    try {
      const res = await fetch("/api/auth/password-reset", {
        method: "POST",
        headers: { "content-type": "application/json", "x-aurora-csrf": "1" },
        body: JSON.stringify({ token: state.token, new_password: pw }),
      });
      if (res.ok) {
        setPw("");
        setConfirm("");
        setState({ phase: "done" });
        return;
      }
      const body = (await res.json().catch(() => null)) as { error?: { code?: string; message?: string; details?: unknown } } | null;
      if (res.status === 429) setError("Too many attempts. Wait a minute and try again.");
      else if (body?.error?.code === "invalid_reset_token") {
        setPw("");
        setConfirm("");
        setState({ phase: "missing" });
        setError(body.error.message ?? "This reset link can no longer be used.");
      } else if (Array.isArray(body?.error?.details) && typeof body.error.details[0] === "string") {
        setError(`${body.error.message ?? "Password does not meet requirements"}: ${body.error.details[0]}.`);
      } else setError(body?.error?.message ?? "The password could not be changed.");
    } catch {
      setError("Cannot reach the server. Check your connection.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4 rounded-xl border border-line bg-surface/80 p-6">
      <div>
        <h1 className="text-lg font-semibold">Choose a new password</h1>
        <p className="mt-1 text-sm text-muted">Setting a new password signs your account out everywhere.</p>
      </div>
      {error && <Notice tone="bad">{error}</Notice>}
      {state.phase === "reading" && <p className="text-sm text-muted">Checking your link…</p>}
      {state.phase === "missing" && (
        <>
          {!error && <Notice tone="warn">Open this page with the reset link your administrator gave you. Each link works once and expires.</Notice>}
          <p className="text-sm text-muted">
            Ask an administrator for a new link, or <Link href="/login" className="text-primary hover:underline">go to sign in</Link>.
          </p>
        </>
      )}
      {state.phase === "done" && (
        <>
          <Notice tone="ok">Your password was changed. Sign in with the new password.</Notice>
          <Link href="/login" className="inline-flex w-full items-center justify-center rounded-lg bg-primary px-3.5 py-2 text-sm font-medium text-slate-950 hover:bg-sky-300">
            Sign in
          </Link>
        </>
      )}
      {state.phase === "ready" && (
        <form onSubmit={onSubmit} noValidate className="space-y-4">
          <Field label="New password" htmlFor="new-password" hint="10+ characters, 3 of: lowercase, uppercase, digits, symbols">
            <Input id="new-password" type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} required />
          </Field>
          <Field label="Repeat the new password" htmlFor="confirm-password">
            <Input id="confirm-password" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required />
          </Field>
          <Button type="submit" variant="primary" loading={busy} className="w-full">
            Set new password
          </Button>
        </form>
      )}
    </div>
  );
}
