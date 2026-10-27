"use client";

import { useState, type FormEvent } from "react";
import useSWR, { useSWRConfig } from "swr";

import { Badge, Button, Card, ConfirmButton, EmptyState, ErrorState, Field, Input, LoadingBlock, Notice, Table, Td, Th } from "@/components/ui";
import { api, apiDownload, saveBlob } from "@/lib/api";
import { daysUntil, exportRows, exportState } from "@/lib/dataRights";
import { fmtBytes, fmtDate, fmtDateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { DeletionSchedule, Job } from "@/lib/types";

/** Start, follow and download full organisation exports (one CSV per table in a ZIP). */
export function DataExportCard() {
  const list = useSWR<Job[]>("/data/exports", {
    refreshInterval: (rows) => (rows?.some((j) => exportState(j).label === "Preparing") ? 2000 : 0),
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const start = async () => {
    setErr(null);
    setBusy(true);
    try {
      await api("/data/exports", { method: "POST" });
      await list.mutate();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  const download = async (job: Job) => {
    setErr(null);
    try {
      const { blob, filename } = await apiDownload(`/data/exports/${job.id}/download`);
      saveBlob(blob, filename);
    } catch (e) {
      setErr(e);
    }
  };

  return (
    <Card
      title="Export all data"
      subtitle="A ZIP with one CSV file per table (products, sales, forecasts, orders, settings and more). Passwords, keys and tokens are left out. Each export can be downloaded for 7 days."
      actions={<Button onClick={() => void start()} loading={busy}>Start export</Button>}
    >
      {err != null && <div className="mb-3"><ErrorState error={err} /></div>}
      {list.error ? (
        <ErrorState error={list.error} onRetry={() => list.mutate()} />
      ) : !list.data ? (
        <LoadingBlock rows={2} label="Loading exports" />
      ) : list.data.length === 0 ? (
        <EmptyState title="No exports yet" />
      ) : (
        <Table caption="Organisation exports">
          <thead>
            <tr>
              <Th>Started</Th>
              <Th>Status</Th>
              <Th className="text-right">Rows</Th>
              <Th className="text-right">Size</Th>
              <Th><span className="sr-only">Download</span></Th>
            </tr>
          </thead>
          <tbody>
            {list.data.map((j) => {
              const s = exportState(j);
              const bytes = typeof j.result?.bytes === "number" ? j.result.bytes : null;
              return (
                <tr key={j.id}>
                  <Td>{fmtDateTime(j.created_at)}</Td>
                  <Td>
                    <Badge tone={s.downloadable ? "ok" : s.label === "Preparing" ? "info" : s.label === "Expired" ? "default" : "bad"}>{s.label}</Badge>
                  </Td>
                  <Td className="text-right tabular-nums">{s.downloadable ? exportRows(j).toLocaleString() : "–"}</Td>
                  <Td className="text-right tabular-nums">{bytes != null ? fmtBytes(bytes) : "–"}</Td>
                  <Td className="text-right">
                    {s.downloadable && (
                      <Button variant="secondary" onClick={() => void download(j)}>Download ZIP</Button>
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

/** Download everything Aurora holds about the signed-in person. */
export function PersonalExportCard() {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const go = async () => {
    setErr(null);
    setBusy(true);
    try {
      const { blob, filename } = await apiDownload("/auth/me/export");
      saveBlob(blob, filename);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Your data" subtitle="Your profile, sign-ins, sessions and the actions recorded in your name, as one JSON file.">
      <Button variant="secondary" onClick={() => void go()} loading={busy}>Download my data</Button>
      <p className="mt-3 text-sm text-muted">To delete your account, ask an administrator of your organisation.</p>
      {err != null && <div className="mt-3"><ErrorState error={err} /></div>}
    </Card>
  );
}

/** Schedule deletion of the whole organisation (7-day grace period) or cancel it. */
export function OrgDeletionCard() {
  const { me } = useSession();
  const { mutate } = useSWRConfig();
  const status = useSWR<{ scheduled: DeletionSchedule | null }>("/organisation/deletion");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  const refresh = async () => {
    await status.mutate();
    await mutate("/auth/me");
  };

  const schedule = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      await api("/organisation/deletion", { method: "POST", body: { confirm_name: name, current_password: password } });
      setName("");
      setPassword("");
      await refresh();
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    await api("/organisation/deletion", { method: "DELETE" });
    await refresh();
  };

  const scheduled = status.data?.scheduled;
  return (
    <Card title="Delete organisation" subtitle="Removes every product, sale, forecast, order, user and file of this organisation. Download an export first if you need the data.">
      {status.error ? (
        <ErrorState error={status.error} onRetry={() => status.mutate()} />
      ) : !status.data ? (
        <LoadingBlock rows={2} label="Loading deletion status" />
      ) : scheduled ? (
        <div className="space-y-3">
          <Notice tone="bad">
            Deletion is scheduled for {fmtDate(scheduled.due_at)} (in {daysUntil(scheduled.due_at)} days), requested by {scheduled.requested_by}. Until then
            everything works as normal and it can be cancelled.
          </Notice>
          <ConfirmButton
            variant="secondary"
            title="Cancel the deletion?"
            body="The organisation and its data are kept."
            confirmLabel="Keep organisation"
            onConfirm={cancel}
          >
            Cancel deletion
          </ConfirmButton>
        </div>
      ) : (
        <form onSubmit={schedule} className="space-y-3">
          <Field label="Organisation name" htmlFor="del-name" hint={me ? `Type “${me.org_name}” to confirm` : undefined}>
            <Input id="del-name" autoComplete="off" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Your password" htmlFor="del-pw">
            <Input id="del-pw" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <Button type="submit" variant="danger" loading={busy} disabled={!me || name.trim() !== me.org_name || !password}>
            Schedule deletion in 7 days
          </Button>
          {err != null && <ErrorState error={err} />}
        </form>
      )}
    </Card>
  );
}

/** Shown on every page while the organisation is scheduled for deletion. */
export function DeletionBanner({ dueAt }: { dueAt: string }) {
  return (
    <div className="mb-4">
      <Notice tone="bad">
        This organisation will be deleted on {fmtDate(dueAt)}. An administrator can cancel this under Settings.
      </Notice>
    </div>
  );
}
