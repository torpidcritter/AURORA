"use client";

import { Check, Copy, KeyRound } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import useSWR from "swr";

import { Badge, Button, Card, ConfirmButton, EmptyState, ErrorState, Field, Input, LoadingBlock, Notice, Select, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { EXPIRY_CHOICES, KEY_ROLES, keyState, rolesFor, webhookUrlProblem } from "@/lib/developers";
import { fmtDateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { ApiKey, ApiKeyRole, Page, WebhookDelivery, WebhookEndpoint, WebhookEvent } from "@/lib/types";

/** Shows a secret once. It lives only in React state; the server keeps only a hash or an encrypted copy. */
export function SecretPanel({ label, value, note, onDone }: { label: string; value: string; note: string; onDone: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const panel = useRef<HTMLElement>(null);
  const [copy, setCopy] = useState<"idle" | "copied" | "manual">("idle");
  useEffect(() => {
    panel.current?.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
  }, []);
  const doCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopy("copied");
    } catch {
      input.current?.select();
      setCopy("manual");
    }
  };
  return (
    <section ref={panel} aria-label={label} className="mb-4 space-y-3 rounded-lg border border-primary/30 bg-primary/5 p-4">
      <div className="flex items-center gap-2 text-sm font-medium text-ink">
        <KeyRound className="h-4 w-4 text-primary" aria-hidden /> {label}
      </div>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input
          ref={input}
          readOnly
          aria-label={`${label} value`}
          value={value}
          onFocus={(e) => e.currentTarget.select()}
          className="w-full rounded-lg border border-line bg-bg/60 px-3 py-2 font-mono text-xs text-ink focus:border-primary focus:outline-none"
        />
        <Button variant="secondary" onClick={() => void doCopy()}>
          {copy === "copied" ? <Check className="h-4 w-4" aria-hidden /> : <Copy className="h-4 w-4" aria-hidden />}
          {copy === "copied" ? "Copied" : "Copy"}
        </Button>
      </div>
      {copy === "manual" && <p className="text-xs text-muted">Copying is blocked here. The text is selected: press Ctrl+C (⌘C on a Mac).</p>}
      <Notice tone="warn">{note} It will not be shown again.</Notice>
      <div className="flex justify-end">
        <Button variant="ghost" onClick={onDone}>Done</Button>
      </div>
    </section>
  );
}

const stateTone = { active: "ok", revoked: "default", expired: "warn" } as const;

export function ApiKeysCard() {
  const { me } = useSession();
  const list = useSWR<ApiKey[]>("/api-keys");
  const allowed = rolesFor(me?.role);
  const [name, setName] = useState("");
  const [role, setRole] = useState<ApiKeyRole>("viewer");
  const [days, setDays] = useState<string>("90");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [fresh, setFresh] = useState<{ name: string; secret: string } | null>(null);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const r = await api<{ key: ApiKey; secret: string }>("/api-keys", {
        method: "POST",
        body: { name: name.trim(), role, expires_in_days: days === "never" ? null : Number(days) },
      });
      setFresh({ name: r.key.name, secret: r.secret });
      setName("");
      await list.mutate();
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (k: ApiKey) => {
    await api(`/api-keys/${k.id}`, { method: "DELETE" });
    await list.mutate();
  };

  return (
    <Card
      title="API keys"
      subtitle="For scripts and other systems. Send the key as “Authorization: Bearer <key>”. A key acts with the role you give it (never more than yours) and stops working if you are deactivated."
      className="mb-6"
    >
      {fresh && (
        <SecretPanel
          label={`New API key “${fresh.name}”`}
          value={fresh.secret}
          note="Anyone with this key can use Aurora's API as this key's role. Store it in a secret manager."
          onDone={() => setFresh(null)}
        />
      )}
      <form onSubmit={create} className="mb-4 grid gap-3 md:grid-cols-4">
        <Field label="Key name" htmlFor="key-name">
          <Input id="key-name" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} placeholder="Nightly ERP sync" />
        </Field>
        <Field label="Key role" htmlFor="key-role">
          <Select id="key-role" value={role} onChange={(e) => setRole(e.target.value as ApiKeyRole)}>
            {KEY_ROLES.filter((r) => allowed.includes(r.key)).map((r) => (
              <option key={r.key} value={r.key}>{r.label}</option>
            ))}
          </Select>
        </Field>
        <Field label="Expires after" htmlFor="key-exp">
          <Select id="key-exp" value={days} onChange={(e) => setDays(e.target.value)}>
            {EXPIRY_CHOICES.map((c) => (
              <option key={c.label} value={c.days == null ? "never" : String(c.days)}>{c.label}</option>
            ))}
          </Select>
        </Field>
        <div className="flex items-end">
          <Button type="submit" loading={busy} disabled={!name.trim()}>Create key</Button>
        </div>
      </form>
      {err != null && <div className="mb-3"><ErrorState error={err} /></div>}
      {list.error ? (
        <ErrorState error={list.error} onRetry={() => list.mutate()} />
      ) : !list.data ? (
        <LoadingBlock rows={2} label="Loading API keys" />
      ) : list.data.length === 0 ? (
        <EmptyState title="No API keys yet" />
      ) : (
        <Table caption="API keys">
          <thead>
            <tr>
              <Th>Name</Th>
              <Th>Key</Th>
              <Th>Role</Th>
              <Th>Last used</Th>
              <Th>Expires</Th>
              <Th>Status</Th>
              <Th><span className="sr-only">Actions</span></Th>
            </tr>
          </thead>
          <tbody>
            {list.data.map((k) => {
              const s = keyState(k);
              return (
                <tr key={k.id}>
                  <Td>
                    <div className="text-ink">{k.name}</div>
                    <div className="text-xs text-faint">by {k.created_by_email ?? "a removed user"} · {fmtDateTime(k.created_at)}</div>
                  </Td>
                  <Td className="font-mono text-xs">{k.prefix}_…</Td>
                  <Td>{k.role}</Td>
                  <Td>{k.last_used_at ? fmtDateTime(k.last_used_at) : "Never"}</Td>
                  <Td>{k.expires_at ? fmtDateTime(k.expires_at) : "Never"}</Td>
                  <Td><Badge tone={stateTone[s]}>{s}</Badge></Td>
                  <Td className="text-right">
                    {s === "active" && (
                      <ConfirmButton
                        title={`Revoke the key “${k.name}”?`}
                        body="Anything using it gets 401 errors straight away. This cannot be undone; create a new key instead."
                        confirmLabel="Revoke key"
                        onConfirm={() => revoke(k)}
                      >
                        Revoke
                      </ConfirmButton>
                    )}
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

const deliveryTone = { pending: "info", succeeded: "ok", failed: "bad" } as const;

export function WebhooksCard() {
  const events = useSWR<WebhookEvent[]>("/integrations/webhooks/events");
  const list = useSWR<WebhookEndpoint[]>("/integrations/webhooks");
  const deliveries = useSWR<Page<WebhookDelivery>>("/integrations/webhooks/deliveries?limit=20", {
    refreshInterval: (p) => (p?.items.some((d) => d.status === "pending") ? 5000 : 0),
  });
  const [url, setUrl] = useState("");
  const [description, setDescription] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [rowErr, setRowErr] = useState<unknown>(null);
  const [fresh, setFresh] = useState<{ label: string; secret: string } | null>(null);

  const refresh = async () => {
    await Promise.all([list.mutate(), deliveries.mutate()]);
  };

  const create = async (e: FormEvent) => {
    e.preventDefault();
    const problem = webhookUrlProblem(url);
    if (problem) return setErr(new Error(problem));
    if (!chosen.length) return setErr(new Error("Choose at least one event"));
    setErr(null);
    setBusy(true);
    try {
      const r = await api<{ endpoint: WebhookEndpoint; secret: string }>("/integrations/webhooks", {
        method: "POST",
        body: { url: url.trim(), description: description.trim(), events: chosen },
      });
      setFresh({ label: `Signing secret for ${r.endpoint.url_hint}`, secret: r.secret });
      setUrl("");
      setDescription("");
      setChosen([]);
      await refresh();
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  };

  const act = async (fn: () => Promise<unknown>) => {
    setRowErr(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setRowErr(e);
    }
  };

  const rotate = async (ep: WebhookEndpoint) => {
    const r = await api<{ secret: string }>(`/integrations/webhooks/${ep.id}/rotate-secret`, { method: "POST" });
    setFresh({ label: `New signing secret for ${ep.url_hint}`, secret: r.secret });
    await refresh();
  };

  const toggle = (key: string) => setChosen((c) => (c.includes(key) ? c.filter((k) => k !== key) : [...c, key]));
  const hint = (id: string) => list.data?.find((e) => e.id === id)?.url_hint ?? "removed endpoint";

  return (
    <Card
      title="Outgoing webhooks"
      subtitle="Aurora POSTs a signed JSON event to your address when something happens. Failed deliveries are retried for about 15 hours; an address that keeps failing is switched off."
    >
      {fresh && (
        <SecretPanel
          label={fresh.label}
          value={fresh.secret}
          note="Use it to check the Aurora-Signature header on each request (see docs/WEBHOOKS.md)."
          onDone={() => setFresh(null)}
        />
      )}
      <form onSubmit={create} className="mb-4 space-y-3">
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Endpoint URL" htmlFor="wh-url" hint="A public https:// address">
            <Input id="wh-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/aurora-events" autoComplete="off" />
          </Field>
          <Field label="Description" htmlFor="wh-desc">
            <Input id="wh-desc" maxLength={200} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="ERP integration" />
          </Field>
        </div>
        <fieldset>
          <legend className="mb-2 text-sm text-muted">Events</legend>
          {!events.data ? (
            <LoadingBlock rows={1} label="Loading events" />
          ) : (
            <div className="grid gap-2 md:grid-cols-2">
              {events.data.map((ev) => (
                <label key={ev.key} className="flex items-start gap-2 text-sm">
                  <input type="checkbox" className="mt-1" checked={chosen.includes(ev.key)} onChange={() => toggle(ev.key)} />
                  <span>
                    <span className="font-mono text-xs text-ink">{ev.key}</span>
                    <span className="block text-xs text-muted">{ev.description}</span>
                  </span>
                </label>
              ))}
            </div>
          )}
        </fieldset>
        <Button type="submit" loading={busy}>Add endpoint</Button>
        {err != null && <ErrorState error={err} />}
      </form>
      {rowErr != null && <div className="mb-3"><ErrorState error={rowErr} /></div>}
      {list.error ? (
        <ErrorState error={list.error} onRetry={() => list.mutate()} />
      ) : !list.data ? (
        <LoadingBlock rows={2} label="Loading endpoints" />
      ) : list.data.length === 0 ? (
        <EmptyState title="No webhook endpoints yet" />
      ) : (
        <Table caption="Webhook endpoints">
          <thead>
            <tr>
              <Th>Endpoint</Th>
              <Th>Events</Th>
              <Th>Status</Th>
              <Th>Last success</Th>
              <Th><span className="sr-only">Actions</span></Th>
            </tr>
          </thead>
          <tbody>
            {list.data.map((ep) => (
              <tr key={ep.id}>
                <Td>
                  <div className="font-mono text-xs text-ink">{ep.url_hint}</div>
                  {ep.description && <div className="text-xs text-faint">{ep.description}</div>}
                </Td>
                <Td className="text-xs">{ep.events.join(", ")}</Td>
                <Td>
                  {ep.active ? <Badge tone="ok">on</Badge> : <Badge tone="warn">off</Badge>}
                  {ep.disabled_reason && <div className="mt-1 text-xs text-muted">{ep.disabled_reason}</div>}
                </Td>
                <Td>{ep.last_success_at ? fmtDateTime(ep.last_success_at) : "–"}</Td>
                <Td>
                  <div className="flex flex-wrap justify-end gap-2">
                    <Button variant="secondary" onClick={() => void act(() => api(`/integrations/webhooks/${ep.id}/test`, { method: "POST" }))}>Send test</Button>
                    <Button variant="ghost" onClick={() => void act(() => api(`/integrations/webhooks/${ep.id}`, { method: "PATCH", body: { active: !ep.active } }))}>
                      {ep.active ? "Turn off" : "Turn on"}
                    </Button>
                    <ConfirmButton
                      variant="secondary"
                      title="Create a new signing secret?"
                      body="The old secret stops working straight away, so update your receiver at the same time."
                      confirmLabel="Rotate secret"
                      onConfirm={() => rotate(ep)}
                    >
                      Rotate secret
                    </ConfirmButton>
                    <ConfirmButton
                      title={`Remove ${ep.url_hint}?`}
                      body="No more events are sent there. Its delivery history is removed too."
                      confirmLabel="Remove endpoint"
                      onConfirm={() => act(() => api(`/integrations/webhooks/${ep.id}`, { method: "DELETE" }))}
                    >
                      Remove
                    </ConfirmButton>
                  </div>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {deliveries.data && deliveries.data.items.length > 0 && (
        <div className="mt-6">
          <Table caption="Recent deliveries">
            <thead>
              <tr>
                <Th>When</Th>
                <Th>Event</Th>
                <Th>Endpoint</Th>
                <Th>Result</Th>
                <Th className="text-right">Tries</Th>
                <Th><span className="sr-only">Retry</span></Th>
              </tr>
            </thead>
            <tbody>
              {deliveries.data.items.map((d) => (
                <tr key={d.id}>
                  <Td>{fmtDateTime(d.created_at)}</Td>
                  <Td className="font-mono text-xs">{d.event}</Td>
                  <Td className="font-mono text-xs">{hint(d.endpoint_id)}</Td>
                  <Td>
                    <Badge tone={deliveryTone[d.status]}>{d.status}</Badge>
                    {d.last_status_code != null && <span className="ml-2 text-xs text-muted">HTTP {d.last_status_code}</span>}
                    {d.status !== "succeeded" && d.last_error && <div className="mt-1 text-xs text-muted">{d.last_error}</div>}
                  </Td>
                  <Td className="text-right tabular-nums">{d.attempts}</Td>
                  <Td className="text-right">
                    {d.status === "failed" && (
                      <Button variant="ghost" onClick={() => void act(() => api(`/integrations/webhooks/deliveries/${d.id}/retry`, { method: "POST" }))}>Retry</Button>
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
