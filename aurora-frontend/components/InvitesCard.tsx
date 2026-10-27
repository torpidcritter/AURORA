"use client";

import { useState, type FormEvent } from "react";
import useSWR from "swr";

import { ResetLinkPanel } from "@/components/ResetLinkPanel";
import { Badge, Button, Card, ConfirmButton, ErrorState, Field, Input, Notice, Select, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import type { Invitation, Role } from "@/lib/types";

const ROLES: Role[] = ["viewer", "operator", "analyst", "admin"];
const TONE: Record<Invitation["state"], "ok" | "warn" | "info" | undefined> = { pending: "info", accepted: "ok", expired: "warn", revoked: undefined };

/** Invite people by e-mail; they choose their own password through a one-time link. */
export function InvitesCard({ onAccepted }: { onAccepted?: () => void }) {
  const list = useSWR<Invitation[]>("/users/invites");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [issued, setIssued] = useState<(Invitation & { invite_url: string; emailed: boolean }) | null>(null);

  const invite = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    setIssued(null);
    setBusy(true);
    try {
      const out = await api<Invitation & { invite_url: string; emailed: boolean }>("/users/invites", { method: "POST", body: { email: email.trim(), role } });
      setIssued(out);
      setEmail("");
      await list.mutate();
      onAccepted?.();
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  };
  const recent = (list.data ?? []).filter((i) => i.state !== "revoked").slice(0, 20);

  return (
    <Card title="Invite people" subtitle="They get a one-time link, valid for 7 days, to choose their own password." className="mb-6">
      <form onSubmit={invite} className="flex flex-wrap items-end gap-3">
        <Field label="Invitee e-mail" htmlFor="inv-email">
          <Input id="inv-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="off" className="w-64" />
        </Field>
        <Field label="Invitee role" htmlFor="inv-role">
          <Select id="inv-role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </Select>
        </Field>
        <Button type="submit" variant="primary" loading={busy} disabled={!email.trim()}>Send invitation</Button>
      </form>
      {err != null && <div className="mt-3"><ErrorState error={err} /></div>}
      {issued && (
        <div className="mt-4">
          {issued.emailed && <div className="mb-2"><Notice tone="ok">The invitation was e-mailed to {issued.email}. The link below is the same one.</Notice></div>}
          <ResetLinkPanel kind="invite" email={issued.email} link={{ reset_url: issued.invite_url, expires_at: issued.expires_at }} onDone={() => setIssued(null)} />
        </div>
      )}
      {recent.length > 0 && (
        <div className="mt-4">
          <Table caption="Invitations">
            <thead>
              <tr>
                <Th>E-mail</Th>
                <Th>Role</Th>
                <Th>State</Th>
                <Th>Expires</Th>
                <Th><span className="sr-only">Actions</span></Th>
              </tr>
            </thead>
            <tbody>
              {recent.map((i) => (
                <tr key={i.id}>
                  <Td>{i.email}</Td>
                  <Td className="capitalize">{i.role}</Td>
                  <Td><Badge tone={TONE[i.state]}>{i.state}</Badge></Td>
                  <Td>{i.state === "pending" ? fmtDateTime(i.expires_at) : "—"}</Td>
                  <Td className="text-right">
                    {i.state === "pending" && (
                      <ConfirmButton
                        title={`Withdraw the invitation for ${i.email}?`}
                        body="The link stops working. You can invite them again later."
                        confirmLabel="Withdraw"
                        onConfirm={async () => {
                          await api(`/users/invites/${i.id}`, { method: "DELETE" });
                          await list.mutate();
                        }}
                      >
                        Withdraw
                      </ConfirmButton>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
    </Card>
  );
}
