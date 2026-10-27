"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { PersonalExportCard } from "@/components/DataRights";
import { TwoFactorCard } from "@/components/TwoFactor";
import { Button, Card, ErrorState, Field, Input, Notice, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { passwordProblem } from "@/lib/validation";

export default function AccountPage() {
  const { me } = useSession();
  const router = useRouter();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const changePassword = async (e: FormEvent) => {
    e.preventDefault();
    const problem = passwordProblem(next);
    if (problem) return setErr(new Error(problem));
    setErr(null);
    setBusy(true);
    try {
      await api("/auth/password", { method: "POST", body: { current_password: current, new_password: next } });
      // Changing the password ends every session, including this one: clear the cookies and sign in again.
      await fetch("/api/auth/logout", { method: "POST", headers: { "x-aurora-csrf": "1" } }).catch(() => undefined);
      router.replace("/login?next=/account");
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader title="Account and security" description={me ? `${me.email} · ${me.role} in ${me.org_name}` : undefined} />
      <div className="grid gap-6 xl:grid-cols-2">
        <TwoFactorCard />
        <Card title="Password" subtitle="Changing it signs you out everywhere, including here.">
          <form onSubmit={changePassword} className="space-y-3">
            <Field label="Current password" htmlFor="pw-cur">
              <Input id="pw-cur" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
            </Field>
            <Field label="New password" htmlFor="pw-new" hint="10+ characters, 3 character types">
              <Input id="pw-new" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
            </Field>
            <Button type="submit" variant="secondary" loading={busy} disabled={!current || !next}>Change password</Button>
            {err != null && <ErrorState error={err} />}
          </form>
          {me?.two_factor_required && <div className="mt-3"><Notice tone="info">Set up two-factor login first to reach the rest of Aurora.</Notice></div>}
        </Card>
        <PersonalExportCard />
      </div>
    </>
  );
}
