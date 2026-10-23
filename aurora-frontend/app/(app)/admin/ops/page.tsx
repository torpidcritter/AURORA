"use client";

import useSWR from "swr";

import { Badge, Card, ErrorState, LoadingBlock, Notice, PageHeader, Stat, Table, Td, Th } from "@/components/ui";
import { fmtDateTime } from "@/lib/format";
import { useSession } from "@/lib/session";

interface OpsStatus {
  components: Record<string, { ok: boolean; latency_ms?: number; error?: string }>;
  queues: Record<string, number | null>;
  workers_responding: number | null;
  jobs_by_status: Record<string, number>;
  recent_failures: { id: string; kind: string; error: string | null; finished_at: string | null }[];
}

export default function OpsPage() {
  const { can } = useSession();
  const s = useSWR<OpsStatus>(can("view_ops") ? "/ops/status" : null, { refreshInterval: 15000 });
  if (!can("view_ops")) return <Notice tone="warn">Your role cannot view system status.</Notice>;
  return (
    <>
      <PageHeader title="System status" description="Dependencies, background queues and recent job failures. Refreshes every 15 seconds." />
      {s.error ? (
        <ErrorState error={s.error} onRetry={() => s.mutate()} />
      ) : !s.data ? (
        <LoadingBlock rows={6} />
      ) : (
        <>
          <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
            {Object.entries(s.data.components).map(([name, c]) => (
              <Stat key={name} label={name} value={c.ok ? "Healthy" : "Down"} tone={c.ok ? "ok" : "bad"} hint={c.ok ? (c.latency_ms != null ? `${c.latency_ms} ms` : undefined) : c.error} />
            ))}
            <Stat
              label="Workers responding"
              value={s.data.workers_responding == null ? "Unknown" : s.data.workers_responding}
              tone={s.data.workers_responding === 0 ? "bad" : "default"}
              hint={s.data.workers_responding == null ? "could not ping workers" : undefined}
            />
          </div>
          <div className="grid gap-6 xl:grid-cols-2">
            <Card title="Queues and jobs">
              <dl className="grid grid-cols-2 gap-2 text-sm">
                {Object.entries(s.data.queues).map(([q, n]) => (
                  <div key={q} className="flex justify-between border-b border-line/50 py-1">
                    <dt className="text-muted">Queue “{q}”</dt>
                    <dd className="tabular-nums">{n == null ? "unknown" : n}</dd>
                  </div>
                ))}
                {Object.entries(s.data.jobs_by_status).map(([st, n]) => (
                  <div key={st} className="flex justify-between border-b border-line/50 py-1">
                    <dt className="text-muted">Jobs {st}</dt>
                    <dd className="tabular-nums">{n}</dd>
                  </div>
                ))}
              </dl>
            </Card>
            <Card title="Recent failures">
              {s.data.recent_failures.length === 0 ? (
                <p className="text-sm text-muted">No failed jobs.</p>
              ) : (
                <Table caption="Recent failed jobs">
                  <thead>
                    <tr>
                      <Th>Job</Th>
                      <Th>Finished</Th>
                      <Th>Error</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {s.data.recent_failures.map((f) => (
                      <tr key={f.id}>
                        <Td><Badge>{f.kind}</Badge></Td>
                        <Td>{fmtDateTime(f.finished_at)}</Td>
                        <Td className="text-xs text-muted">{f.error ?? "—"}</Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              )}
            </Card>
          </div>
          <p className="mt-4 text-xs text-faint">Prometheus metrics are served by the API at /metrics and are not exposed through this app.</p>
        </>
      )}
    </>
  );
}
