"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { takeResetToken } from "@/app/reset-password/ResetPasswordForm";
import { Button, Field, Input, Notice } from "@/components/ui";
import { passwordProblem } from "@/lib/validation";

interface InviteInfo {
  email: string;
  role: string;
  org_name: string;
  expires_at: string;
}

type State =
  | { phase: "reading" }
  | { phase: "invalid"; message: string }
  | { phase: "ready"; token: string; info: InviteInfo }
  | { phase: "done"; email: string };

async function post(body: Record<string, string>): Promise<{ ok: boolean; status: number; data: Record<string, unknown> | null }> {
  const res = await fetch("/api/auth/invite", {
    method: "POST",
    headers: { "content-type": "application/json", "x-aurora-csrf": "1" },
    body: JSON.stringify(body),
  });
  return { ok: res.ok, status: res.status, data: (await res.json().catch(() => null)) as Record<string, unknown> | null };
}

const errorOf = (data: Record<string, unknown> | null) =>
  (data?.error as { code?: string; message?: string; details?: unknown } | undefined) ?? undefined;

export function AcceptInviteForm() {
  const [state, setState] = useState<State>({ phase: "reading" });
  const [name, setName] = useState("");
  const [pw, setPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const taken = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    if (taken.current === undefined) taken.current = takeResetToken(window.location, window.history);
    const token = taken.current;
    if (!token) {
      setState({ phase: "invalid", message: "Open this page with the invitation link you were sent." });
      return;
    }
    post({ action: "inspect", token })
      .then(({ ok, data }) =>
        setState(
          ok && data
            ? { phase: "ready", token, info: data as unknown as InviteInfo }
            : { phase: "invalid", message: errorOf(data)?.message ?? "This invitation cannot be used." },
        ),
      )
      .catch(() => setState({ phase: "invalid", message: "Cannot reach the server. Check your connection and reload." }));
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
      const { ok, status, data } = await post({ action: "accept", token: state.token, full_name: name.trim(), password: pw });
      if (ok) {
        setState({ phase: "done", email: state.info.email });
        return;
      }
      const err = errorOf(data);
      if (status === 429) setError("Too many attempts. Wait a minute and try again.");
      else if (err?.code === "invalid_invitation") setState({ phase: "invalid", message: err.message ?? "This invitation can no longer be used." });
      else if (Array.isArray(err?.details) && typeof err.details[0] === "string") setError(`${err.message}: ${err.details[0]}.`);
      else setError(err?.message ?? "The account could not be created.");
    } catch {
      setError("Cannot reach the server. Check your connection.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4 rounded-xl border border-line bg-surface/80 p-6">
      <h1 className="text-lg font-semibold">Join your team on Aurora</h1>
      {error && <Notice tone="bad">{error}</Notice>}
      {state.phase === "reading" && <p className="text-sm text-muted">Checking your invitation…</p>}
      {state.phase === "invalid" && (
        <>
          <Notice tone="warn">{state.message}</Notice>
          <p className="text-sm text-muted">
            Ask your administrator for a new invitation, or <Link href="/login" className="text-primary hover:underline">go to sign in</Link>.
          </p>
        </>
      )}
      {state.phase === "done" && (
        <>
          <Notice tone="ok">Your account {state.email} is ready. Sign in with your new password.</Notice>
          <Link href="/login" className="inline-flex w-full items-center justify-center rounded-lg bg-primary px-3.5 py-2 text-sm font-medium text-slate-950 hover:bg-sky-300">
            Sign in
          </Link>
        </>
      )}
      {state.phase === "ready" && (
        <form onSubmit={onSubmit} noValidate className="space-y-4">
          <p className="text-sm text-muted">
            You are invited to <span className="text-ink">{state.info.org_name}</span> as <span className="text-ink">{state.info.role}</span>, signing in as{" "}
            <span className="text-ink">{state.info.email}</span>.
          </p>
          <Field label="Your name" htmlFor="inv-name">
            <Input id="inv-name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Choose a password" htmlFor="inv-pw" hint="10+ characters, 3 of: lowercase, uppercase, digits, symbols">
            <Input id="inv-pw" type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} required />
          </Field>
          <Field label="Repeat the password" htmlFor="inv-pw2">
            <Input id="inv-pw2" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required />
          </Field>
          <Button type="submit" variant="primary" loading={busy} className="w-full">Create my account</Button>
        </form>
      )}
    </div>
  );
}
