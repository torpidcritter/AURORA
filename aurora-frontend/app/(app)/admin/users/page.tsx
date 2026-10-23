"use client";

import { useState, type FormEvent } from "react";
import useSWR from "swr";

import { Badge, Button, Card, ConfirmButton, EmptyState, ErrorState, Field, Input, LoadingBlock, Notice, PageHeader, Pagination, Select, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Page, Role, User } from "@/lib/types";
import { passwordProblem } from "@/lib/validation";

const ROLES: Role[] = ["viewer", "operator", "analyst", "admin"];
const LIMIT = 50;

export default function UsersPage() {
  const { me, can } = useSession();
  const [offset, setOffset] = useState(0);
  const list = useSWR<Page<User>>(can("manage_users") ? `/users?limit=${LIMIT}&offset=${offset}` : null);
  const [f, setF] = useState({ email: "", full_name: "", password: "", role: "viewer" as Role });
  const [err, setErr] = useState<unknown>(null);
  const [rowErr, setRowErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  if (!can("manage_users")) return <Notice tone="warn">Only administrators can manage users.</Notice>;

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    setMsg(null);
    const problem = passwordProblem(f.password);
    if (problem) return setErr(new Error(problem));
    setBusy(true);
    try {
      await api("/auth/register", { method: "POST", body: { ...f, email: f.email.trim() } });
      setMsg(`Created ${f.email.trim()}. Share the password with them through a secure channel.`);
      setF({ email: "", full_name: "", password: "", role: "viewer" });
      await list.mutate();
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  };

  const patch = async (u: User, body: Partial<Pick<User, "role" | "is_active">>) => {
    setRowErr(null);
    try {
      await api(`/users/${u.id}`, { method: "PATCH", body });
      await list.mutate();
    } catch (e) {
      setRowErr(e);
    }
  };

  return (
    <>
      <PageHeader title="Users" description="People in your organisation and what they can do. Role changes and deactivation sign the user out everywhere." />
      <Card title="Add a user" className="mb-6">
        <form onSubmit={create} className="grid gap-3 md:grid-cols-5">
          <Field label="Email" htmlFor="ue"><Input id="ue" type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} autoComplete="off" /></Field>
          <Field label="Full name" htmlFor="un"><Input id="un" value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
          <Field label="Initial password" htmlFor="up" hint="10+ characters, 3 character types">
            <Input id="up" type="password" required value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} autoComplete="new-password" />
          </Field>
          <Field label="Role" htmlFor="ur">
            <Select id="ur" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value as Role })}>
              {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
            </Select>
          </Field>
          <div className="flex items-end"><Button type="submit" variant="primary" loading={busy}>Create user</Button></div>
        </form>
        {err != null && <div className="mt-3"><ErrorState error={err} /></div>}
        {msg && <div className="mt-3"><Notice tone="ok">{msg}</Notice></div>}
      </Card>
      <Card title="Members">
        {rowErr != null && <div className="mb-3"><ErrorState error={rowErr} /></div>}
        {list.error ? (
          <ErrorState error={list.error} onRetry={() => list.mutate()} />
        ) : !list.data ? (
          <LoadingBlock />
        ) : list.data.items.length === 0 ? (
          <EmptyState title="No users" />
        ) : (
          <>
            <Table caption="Users">
              <thead>
                <tr>
                  <Th>User</Th>
                  <Th>Role</Th>
                  <Th>Status</Th>
                  <Th>Last sign-in</Th>
                  <Th><span className="sr-only">Actions</span></Th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((u) => {
                  const self = u.id === me?.id;
                  return (
                    <tr key={u.id}>
                      <Td>
                        <div className="text-ink">{u.full_name || u.email}</div>
                        <div className="text-xs text-faint">{u.email}{self ? " (you)" : ""}</div>
                      </Td>
                      <Td>
                        <label className="sr-only" htmlFor={`role-${u.id}`}>Role for {u.email}</label>
                        <Select id={`role-${u.id}`} value={u.role} disabled={self} onChange={(e) => void patch(u, { role: e.target.value as Role })} className="w-32">
                          {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                        </Select>
                      </Td>
                      <Td>{u.is_active ? <Badge tone="ok">active</Badge> : <Badge tone="warn">deactivated</Badge>}</Td>
                      <Td>{fmtDateTime(u.last_login_at)}</Td>
                      <Td className="text-right">
                        {!self &&
                          (u.is_active ? (
                            <ConfirmButton
                              title={`Deactivate ${u.email}?`}
                              body="They are signed out immediately and cannot sign in until reactivated. Their history and audit records are kept."
                              confirmLabel="Deactivate"
                              onConfirm={() => patch(u, { is_active: false })}
                            >
                              Deactivate
                            </ConfirmButton>
                          ) : (
                            <Button variant="ghost" onClick={() => void patch(u, { is_active: true })}>Reactivate</Button>
                          ))}
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
            <Pagination total={list.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
          </>
        )}
      </Card>
    </>
  );
}
