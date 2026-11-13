"use client";

import { useState } from "react";
import useSWR from "swr";

import { DigestScheduleForm } from "@/components/DigestScheduleForm";
import { Badge, Button, Card, ConfirmButton, EmptyState, ErrorState, LoadingBlock, PageHeader, Pagination, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { CHANNELS, SECTIONS, whenLabel } from "@/lib/digests";
import { fmtDateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { DigestRun, DigestSchedule, DigestSection, Page } from "@/lib/types";

const STATUS_TONE = { delivered: "ok", failed: "bad", in_app: "info" } as const;
const channelLabel = (c: string) => CHANNELS.find((x) => x.key === c)?.label ?? c;
const sectionLabel = (s: DigestSection) => SECTIONS.find((x) => x.key === s)?.label ?? s;

export default function DigestsPage() {
  const { can } = useSession();
  const admin = can("manage_settings");
  const schedules = useSWR<DigestSchedule[]>("/digests/schedules");
  const [offset, setOffset] = useState(0);
  const runs = useSWR<Page<DigestRun>>(`/digests?limit=20&offset=${offset}`);
  const [open, setOpen] = useState<DigestRun | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const act = async (id: string, fn: () => Promise<unknown>) => {
    setErr(null);
    setBusy(id);
    try {
      await fn();
      await Promise.all([schedules.mutate(), runs.mutate()]);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  };

  const view = (id: string) => act(`view-${id}`, async () => setOpen(await api<DigestRun>(`/digests/${id}`)));
  const preview = () =>
    act("preview", async () => {
      const r = await api<{ text: string[] }>("/digests/preview", { method: "POST", body: { sections: SECTIONS.map((s) => s.key) } });
      setOpen({ id: "preview", schedule_id: null, title: "Preview", channel: "in_app", status: "in_app", error: null, trigger: "manual", created_at: new Date().toISOString(), text: r.text });
    });

  return (
    <>
      <PageHeader
        title="Digests"
        description="A short daily or weekly summary of stockout risk, overstock, forecast accuracy, alerts and purchasing, kept here and optionally sent to Slack, a webhook or e-mail."
        actions={<Button onClick={() => void preview()} loading={busy === "preview"}>Preview today&apos;s digest</Button>}
      />
      {admin && (
        <Card title="New digest" className="mb-6">
          <DigestScheduleForm onCreated={async () => void (await schedules.mutate())} />
        </Card>
      )}
      {err != null && <div className="mb-4"><ErrorState error={err} /></div>}
      {open?.text && (
        <Card title={open.title} className="mb-6" actions={<Button variant="ghost" onClick={() => setOpen(null)}>Close</Button>}>
          <div aria-label="Digest content" className="space-y-1 whitespace-pre-wrap font-mono text-xs text-ink">
            {open.text.map((line, i) => <div key={i}>{line || " "}</div>)}
          </div>
          {open.error && <p className="mt-3 text-xs text-bad">Delivery failed: {open.error}</p>}
        </Card>
      )}
      <Card title="Schedules" className="mb-6">
        {schedules.error ? (
          <ErrorState error={schedules.error} onRetry={() => schedules.mutate()} />
        ) : !schedules.data ? (
          <LoadingBlock rows={2} />
        ) : schedules.data.length === 0 ? (
          <EmptyState title="No digests scheduled">{admin ? "Create one above." : "An admin can schedule one."}</EmptyState>
        ) : (
          <Table caption="Digest schedules">
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>When</Th>
                <Th>Sent to</Th>
                <Th>Includes</Th>
                <Th>Next</Th>
                {admin && <Th><span className="sr-only">Actions</span></Th>}
              </tr>
            </thead>
            <tbody>
              {schedules.data.map((s) => (
                <tr key={s.id}>
                  <Td><span className="font-medium text-ink">{s.name}</span> {!s.is_active && <Badge tone="warn">paused</Badge>}</Td>
                  <Td>{whenLabel(s)}</Td>
                  <Td>{channelLabel(s.channel)}{s.target_hint && <div className="text-xs text-faint">{s.target_hint}</div>}</Td>
                  <Td className="text-xs">{s.sections.map(sectionLabel).join(", ")}</Td>
                  <Td className="whitespace-nowrap">{s.is_active ? fmtDateTime(s.next_run_at) : "—"}</Td>
                  {admin && (
                    <Td className="whitespace-nowrap text-right">
                      <Button variant="ghost" loading={busy === `send-${s.id}`} onClick={() => void act(`send-${s.id}`, async () => setOpen(await api<DigestRun>(`/digests/schedules/${s.id}/send`, { method: "POST" })))}>
                        Send now
                      </Button>
                      <Button variant="ghost" onClick={() => void act(`pause-${s.id}`, () => api(`/digests/schedules/${s.id}`, { method: "PATCH", body: { expected_version: s.version, is_active: !s.is_active } }))}>
                        {s.is_active ? "Pause" : "Resume"}
                      </Button>
                      <ConfirmButton title={`Delete "${s.name}"?`} body="Digests already sent stay in the list below." confirmLabel="Delete" onConfirm={() => act(`del-${s.id}`, () => api(`/digests/schedules/${s.id}`, { method: "DELETE" }))}>
                        Delete
                      </ConfirmButton>
                    </Td>
                  )}
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      <Card title="Recent digests">
        {runs.error ? (
          <ErrorState error={runs.error} onRetry={() => runs.mutate()} />
        ) : !runs.data ? (
          <LoadingBlock rows={3} />
        ) : runs.data.items.length === 0 ? (
          <EmptyState title="No digests yet" />
        ) : (
          <>
            <Table caption="Recent digests">
              <thead>
                <tr>
                  <Th>Digest</Th>
                  <Th>Channel</Th>
                  <Th>Status</Th>
                  <Th>When</Th>
                  <Th><span className="sr-only">Open</span></Th>
                </tr>
              </thead>
              <tbody>
                {runs.data.items.map((r) => (
                  <tr key={r.id}>
                    <Td>{r.title}</Td>
                    <Td>{channelLabel(r.channel)}</Td>
                    <Td><Badge tone={STATUS_TONE[r.status]}>{r.status === "in_app" ? "in app" : r.status}</Badge></Td>
                    <Td className="whitespace-nowrap">{fmtDateTime(r.created_at)}</Td>
                    <Td className="text-right"><Button variant="ghost" loading={busy === `view-${r.id}`} onClick={() => void view(r.id)}>View</Button></Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <Pagination total={runs.data.total} limit={20} offset={offset} onChange={setOffset} />
          </>
        )}
      </Card>
    </>
  );
}
