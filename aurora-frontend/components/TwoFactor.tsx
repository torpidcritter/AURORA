"use client";

import QRCode from "qrcode";
import { useEffect, useState, type FormEvent } from "react";
import useSWR from "swr";

import { Badge, Button, Card, ConfirmButton, ErrorState, Field, Input, LoadingBlock, Notice } from "@/components/ui";
import { api, saveBlob } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import { cleanCode, codeProblem, groupSecret, recoveryCodesText } from "@/lib/twoFactor";
import type { TwoFactorStatus } from "@/lib/types";

/** Recovery codes, shown once after they are created. */
export function RecoveryCodes({ codes, email, onDone }: { codes: string[]; email: string; onDone: () => void }) {
  return (
    <Notice tone="warn">
      <p className="mb-2 font-medium">Save these recovery codes now. They are not shown again.</p>
      <p className="mb-2 text-xs">Each one signs you in once if you lose your phone.</p>
      <ul aria-label="Recovery codes" className="mb-3 grid grid-cols-2 gap-1 font-mono text-sm">
        {codes.map((c) => <li key={c}>{c}</li>)}
      </ul>
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" onClick={() => saveBlob(new Blob([recoveryCodesText(codes, email)], { type: "text/plain" }), "aurora-recovery-codes.txt")}>
          Download codes
        </Button>
        <Button variant="primary" onClick={onDone}>I have saved them</Button>
      </div>
    </Notice>
  );
}

function SetupFlow({ email, onEnabled }: { email: string; onEnabled: (codes: string[]) => void }) {
  const [password, setPassword] = useState("");
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!setup) return;
    let live = true;
    QRCode.toDataURL(setup.otpauth_uri, { margin: 1, width: 192 })
      .then((url) => live && setQr(url))
      .catch(() => live && setQr(null));
    return () => {
      live = false;
    };
  }, [setup]);

  const start = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      setSetup(await api<{ secret: string; otpauth_uri: string }>("/auth/two-factor/setup", { method: "POST", body: { current_password: password } }));
      setPassword("");
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  };
  const confirm = async (e: FormEvent) => {
    e.preventDefault();
    const problem = codeProblem(code);
    if (problem) return setErr(new Error(problem));
    setErr(null);
    setBusy(true);
    try {
      const out = await api<{ recovery_codes: string[] }>("/auth/two-factor/enable", { method: "POST", body: { code: cleanCode(code) } });
      onEnabled(out.recovery_codes);
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  };

  if (!setup) {
    return (
      <form onSubmit={start} className="flex flex-wrap items-end gap-3">
        <Field label="Current password" htmlFor="tf-pw" hint="Confirms it is you before linking an app.">
          <Input id="tf-pw" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </Field>
        <Button type="submit" variant="primary" loading={busy} disabled={!password}>Set up two-factor login</Button>
        {err != null && <div className="w-full"><ErrorState error={err} /></div>}
      </form>
    );
  }
  return (
    <form onSubmit={confirm} className="space-y-3">
      <p className="text-sm text-muted">
        Scan this with an authenticator app (for example 1Password, Google Authenticator or Microsoft Authenticator) for {email}, then enter the code it shows.
      </p>
      <div className="flex flex-wrap items-center gap-4">
        {/* eslint-disable-next-line @next/next/no-img-element -- a generated data: URL, nothing to optimise */}
        {qr ? <img src={qr} alt="QR code for your authenticator app" width={192} height={192} className="rounded bg-white p-1" /> : null}
        <div className="text-sm">
          <div className="text-muted">Or enter this key by hand:</div>
          <code className="font-mono text-ink" aria-label="Setup key">{groupSecret(setup.secret)}</code>
        </div>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Code from the app" htmlFor="tf-code">
          <Input id="tf-code" inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} className="w-32" />
        </Field>
        <Button type="submit" variant="primary" loading={busy}>Turn on</Button>
      </div>
      {err != null && <ErrorState error={err} />}
    </form>
  );
}

function CodeAction({ label, withPassword, onSubmit }: { label: string; withPassword: boolean; onSubmit: (code: string, password: string) => Promise<void> }) {
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const problem = codeProblem(code);
    if (problem) return setErr(new Error(problem));
    setErr(null);
    setBusy(true);
    try {
      await onSubmit(cleanCode(code), password);
      setCode("");
      setPassword("");
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  };
  const id = label.replace(/\W+/g, "-").toLowerCase();
  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
      {withPassword && (
        <Field label="Current password" htmlFor={`${id}-pw`}>
          <Input id={`${id}-pw`} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
      )}
      <Field label={`Code for: ${label}`} htmlFor={`${id}-code`}>
        <Input id={`${id}-code`} inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} className="w-32" />
      </Field>
      <Button type="submit" variant="secondary" loading={busy}>{label}</Button>
      {err != null && <div className="w-full"><ErrorState error={err} /></div>}
    </form>
  );
}

/** The signed-in user's two-factor login: set up, recovery codes, turn off. */
export function TwoFactorCard() {
  const { me } = useSession();
  const status = useSWR<TwoFactorStatus>("/auth/two-factor");
  const [codes, setCodes] = useState<string[] | null>(null);
  const s = status.data;
  const refresh = async () => {
    await status.mutate();
  };

  return (
    <Card title="Two-factor login" subtitle="A code from your phone in addition to your password when you sign in.">
      {status.error ? (
        <ErrorState error={status.error} onRetry={() => status.mutate()} />
      ) : !s || !me ? (
        <LoadingBlock rows={3} />
      ) : (
        <div className="space-y-4">
          {s.required_by_organisation && !s.enabled && (
            <Notice tone="warn">Your organisation requires two-factor login. Set it up to continue using Aurora.</Notice>
          )}
          {codes && <RecoveryCodes codes={codes} email={me.email} onDone={() => setCodes(null)} />}
          {s.enabled ? (
            <>
              <p className="text-sm">
                <Badge tone="ok">On</Badge> since {fmtDateTime(s.enabled_at)} · {s.recovery_codes_left} recovery code{s.recovery_codes_left === 1 ? "" : "s"} left
              </p>
              {s.recovery_codes_left <= 3 && <Notice tone="warn">You are running low on recovery codes. Create new ones below.</Notice>}
              <CodeAction
                label="New recovery codes"
                withPassword={false}
                onSubmit={async (code) => {
                  const out = await api<{ recovery_codes: string[] }>("/auth/two-factor/recovery-codes", { method: "POST", body: { code } });
                  setCodes(out.recovery_codes);
                  await refresh();
                }}
              />
              {!s.required_by_organisation && (
                <CodeAction
                  label="Turn off two-factor"
                  withPassword
                  onSubmit={async (code, password) => {
                    await api("/auth/two-factor/disable", { method: "POST", body: { code, current_password: password } });
                    await refresh();
                  }}
                />
              )}
            </>
          ) : (
            <SetupFlow
              email={me.email}
              onEnabled={async (c) => {
                setCodes(c);
                await refresh();
              }}
            />
          )}
        </div>
      )}
    </Card>
  );
}

/** Organisation rule: everyone must use two-factor login. */
export function TwoFactorPolicyCard() {
  const status = useSWR<TwoFactorStatus>("/auth/two-factor");
  const [err, setErr] = useState<unknown>(null);
  const required = status.data?.required_by_organisation ?? false;
  const set = async (value: boolean) => {
    setErr(null);
    try {
      await api("/auth/two-factor/policy", { method: "PUT", body: { required: value } });
      await status.mutate();
    } catch (e) {
      setErr(e);
    }
  };
  return (
    <Card title="Two-factor requirement" className="mb-6">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span>
          {required ? <Badge tone="ok">Required</Badge> : <Badge>Optional</Badge>}{" "}
          {required
            ? "Everyone must set up two-factor login before they can use Aurora."
            : "Each person decides on their account page. You must have it on yourself before requiring it."}
        </span>
        {required ? (
          <ConfirmButton title="Make two-factor login optional?" body="People can then turn it off on their account page." confirmLabel="Make optional" onConfirm={() => set(false)}>
            Make optional
          </ConfirmButton>
        ) : (
          <ConfirmButton
            variant="secondary"
            title="Require two-factor login for everyone?"
            body="People without it are sent to set it up the next time they use Aurora, and can do nothing else until they have."
            confirmLabel="Require it"
            onConfirm={() => set(true)}
          >
            Require for everyone
          </ConfirmButton>
        )}
      </div>
      {err != null && <div className="mt-3"><ErrorState error={err} /></div>}
    </Card>
  );
}
