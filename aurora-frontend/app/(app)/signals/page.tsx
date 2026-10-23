"use client";

import { useState, type FormEvent } from "react";
import useSWR from "swr";

import { Badge, Button, Card, ConfirmButton, EmptyState, ErrorState, Field, Input, LoadingBlock, Notice, PageHeader, Select, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Signal } from "@/lib/types";

const TYPES = ["holiday", "promotion", "weather", "festival", "event", "other"];

export default function SignalsPage() {
  const { can } = useSession();
  const editable = can("manage_signals");
  const list = useSWR<Signal[]>("/signals");
  const [f, setF] = useState({ name: "", signal_type: "promotion", start_date: "", end_date: "", magnitude: "0.2", sku: "" });
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [upload, setUpload] = useState<{ created: number; error_count: number; errors: unknown[] } | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    const m = Number(f.magnitude);
    if (!f.name.trim()) return setErr(new Error("Name is required"));
    if (!f.start_date || !f.end_date || f.end_date < f.start_date) return setErr(new Error("End date must be on or after the start date"));
    if (!Number.isFinite(m) || m < -1 || m > 1) return setErr(new Error("Magnitude must be between -1 and 1"));
    setBusy(true);
    try {
      await api("/signals", { method: "POST", body: { ...f, name: f.name.trim(), magnitude: m, sku: f.sku.trim() || null } });
      setF({ ...f, name: "" });
      await list.mutate();
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  };

  const onUpload = async (file: File | undefined) => {
    if (!file) return;
    setErr(null);
    const form = new FormData();
    form.append("file", file);
    try {
      setUpload(await api("/signals/upload", { method: "POST", form }));
      await list.mutate();
    } catch (e2) {
      setErr(e2);
    }
  };

  return (
    <>
      <PageHeader
        title="External signals"
        description="Holidays, promotions, weather and other events. Signals are shown next to forecasts for context and change numbers only in a scenario where you choose a sensitivity."
      />
      {editable && (
        <Card title="Add a signal" className="mb-6">
          <form onSubmit={submit} className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
            <Field label="Name" htmlFor="sn"><Input id="sn" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} maxLength={200} /></Field>
            <Field label="Type" htmlFor="st">
              <Select id="st" value={f.signal_type} onChange={(e) => setF({ ...f, signal_type: e.target.value })}>
                {TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
              </Select>
            </Field>
            <Field label="Start" htmlFor="ss"><Input id="ss" type="date" value={f.start_date} onChange={(e) => setF({ ...f, start_date: e.target.value })} /></Field>
            <Field label="End" htmlFor="se"><Input id="se" type="date" value={f.end_date} onChange={(e) => setF({ ...f, end_date: e.target.value })} /></Field>
            <Field label="Magnitude (−1 to 1)" htmlFor="sm" hint="+0.2 means about 20% more demand at full sensitivity">
              <Input id="sm" inputMode="decimal" value={f.magnitude} onChange={(e) => setF({ ...f, magnitude: e.target.value })} />
            </Field>
            <Field label="SKU (optional)" htmlFor="ssku" hint="Blank applies to all products">
              <Input id="ssku" value={f.sku} onChange={(e) => setF({ ...f, sku: e.target.value })} />
            </Field>
            <div className="flex items-center gap-3 md:col-span-3 xl:col-span-6">
              <Button type="submit" variant="primary" loading={busy}>Add signal</Button>
              <label className="cursor-pointer text-sm text-primary hover:underline">
                Or upload a CSV
                <input type="file" accept=".csv,text/csv" className="sr-only" onChange={(e) => void onUpload(e.target.files?.[0])} />
              </label>
              <span className="text-xs text-faint">Columns: name, signal_type, start_date, end_date, magnitude, optional sku</span>
            </div>
          </form>
          {err != null && <div className="mt-3"><ErrorState error={err} /></div>}
          {upload && (
            <div className="mt-3">
              <Notice tone={upload.error_count ? "warn" : "ok"}>
                Imported {upload.created} signal(s){upload.error_count ? `; ${upload.error_count} row(s) rejected` : ""}.
              </Notice>
            </div>
          )}
        </Card>
      )}
      <Card title="Signals">
        {list.error ? (
          <ErrorState error={list.error} onRetry={() => list.mutate()} />
        ) : !list.data ? (
          <LoadingBlock />
        ) : list.data.length === 0 ? (
          <EmptyState title="No signals" />
        ) : (
          <Table caption="External signals">
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>Type</Th>
                <Th>Dates</Th>
                <Th className="text-right">Magnitude</Th>
                <Th>Scope</Th>
                {editable && <Th><span className="sr-only">Actions</span></Th>}
              </tr>
            </thead>
            <tbody>
              {list.data.map((s) => (
                <tr key={s.id}>
                  <Td>{s.name}</Td>
                  <Td><Badge>{s.signal_type ?? s.type}</Badge></Td>
                  <Td>{fmtDate(s.start_date)} – {fmtDate(s.end_date)}</Td>
                  <Td className="text-right tabular-nums">{s.magnitude > 0 ? "+" : ""}{s.magnitude}</Td>
                  <Td>{s.product_id ? "One product" : "All products"}</Td>
                  {editable && (
                    <Td className="text-right">
                      <ConfirmButton
                        title="Delete signal?"
                        body={`“${s.name}” will be removed. Scenarios that used it will no longer apply it.`}
                        confirmLabel="Delete"
                        onConfirm={async () => {
                          await api(`/signals/${s.id}`, { method: "DELETE" });
                          await list.mutate();
                        }}
                      >
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
    </>
  );
}
