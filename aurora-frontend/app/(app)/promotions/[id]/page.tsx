"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useState } from "react";
import useSWR, { useSWRConfig } from "swr";

import { Badge, Button, Card, ConfirmButton, ErrorState, Field, Input, LoadingBlock, PageHeader, Stat, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDate, fmtInt } from "@/lib/format";
import { MAX_UPLIFT, SOURCE_LABEL, STATUS_TONE, fmtLift } from "@/lib/promotions";
import { useSession } from "@/lib/session";
import type { PromotionDetail } from "@/lib/types";

export default function PromotionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const d = useSWR<PromotionDetail>(`/promotions/${id}`);
  const { can } = useSession();
  const router = useRouter();
  const { mutate } = useSWRConfig();
  const [uplift, setUplift] = useState<string | null>(null);
  const [notes, setNotes] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const editable = can("manage_overrides");

  if (d.error) return <ErrorState error={d.error} onRetry={() => d.mutate()} />;
  if (!d.data) return <LoadingBlock rows={5} label="Loading promotion" />;
  const p = d.data;
  const finished = p.status === "finished";

  const save = async (body: Record<string, unknown>) => {
    setErr(null);
    setBusy(true);
    try {
      await d.mutate(await api<PromotionDetail>(`/promotions/${id}`, { method: "PATCH", body: { ...body, expected_version: p.version } }), { revalidate: false });
      await mutate("/promotions");
      setUplift(null);
      setNotes(null);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  const saveUplift = () => {
    const v = Number(uplift);
    if (!Number.isFinite(v) || v <= 0 || v > MAX_UPLIFT) return setErr(new Error(`Uplift must be above 0 and at most ${MAX_UPLIFT}×`));
    return save({ uplift: v });
  };

  const m = p.measurement;
  return (
    <>
      <PageHeader
        title={p.name}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Badge tone={STATUS_TONE[p.status]}>{p.status}</Badge>
            {fmtDate(p.start_date)} – {fmtDate(p.end_date)} · {p.product_count} product{p.product_count === 1 ? "" : "s"}
            {p.category ? ` (category ${p.category})` : ""}
            {p.discount_pct != null ? ` · ${p.discount_pct}% off` : ""}
          </span>
        }
        actions={<Link href="/promotions" className="text-sm text-primary hover:underline">All promotions</Link>}
      />
      {!finished && (
        <Card title="Uplift used for planning" subtitle="Forecast demand on the promotion's days is multiplied by this, unless a planner override covers the day.">
          <div className="space-y-3 text-sm">
            <div className="flex flex-wrap items-baseline gap-3">
              <span className="text-lg text-ink tabular-nums">{fmtLift(p.uplift)}</span>
              <span className="text-muted">{SOURCE_LABEL[p.uplift_source]}</span>
            </div>
            {p.estimate && (
              <p className="text-muted">
                Estimate: {p.estimate.uplift == null ? `none (${p.estimate.basis})` : `${fmtLift(p.estimate.uplift)} from ${p.estimate.basis}`}.
              </p>
            )}
            {editable &&
              (uplift === null ? (
                <Button variant="secondary" onClick={() => setUplift(String(p.uplift ?? p.estimate?.uplift ?? ""))}>Change uplift</Button>
              ) : (
                <div className="flex flex-wrap items-end gap-2">
                  <Field label="Uplift (×)" htmlFor="pd-uplift">
                    <Input id="pd-uplift" type="number" min={0.1} max={10} step={0.1} value={uplift} onChange={(e) => setUplift(e.target.value)} />
                  </Field>
                  <Button onClick={saveUplift} loading={busy}>Save uplift</Button>
                  <Button variant="ghost" onClick={() => setUplift(null)}>Cancel</Button>
                </div>
              ))}
          </div>
        </Card>
      )}
      {m && (
        <Card title={finished ? "What happened" : "So far"} subtitle="Average daily sales during the promotion compared with the 28 days before it. The dip is the 14 days after, compared with before. A simple comparison: trend, season and other events are not corrected for.">
          <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Stat label="Lift" value={fmtLift(m.lift)} />
            <Stat label="Extra units sold" value={m.incremental_units == null ? "—" : fmtInt(m.incremental_units)} />
            <Stat label="Products measured" value={`${m.measured_products} of ${m.products.length}`} />
          </div>
          <Table caption="Results by product">
            <thead>
              <tr>
                <Th>Product</Th>
                <Th className="text-right">Before (per day)</Th>
                <Th className="text-right">During (per day)</Th>
                <Th className="text-right">Lift</Th>
                <Th className="text-right">Dip after</Th>
              </tr>
            </thead>
            <tbody>
              {m.products.map((r) => (
                <tr key={r.product_id}>
                  <Td><Link href={`/products/${r.product_id}`} className="text-primary hover:underline">{r.sku ?? r.product_id}</Link></Td>
                  <Td className="text-right tabular-nums">{r.baseline_per_day ?? "—"}</Td>
                  <Td className="text-right tabular-nums">{r.during_per_day ?? "—"}</Td>
                  <Td className="text-right tabular-nums">{r.lift == null ? (r.note ?? "—") : fmtLift(r.lift)}</Td>
                  <Td className="text-right tabular-nums">{r.dip == null ? "—" : fmtLift(r.dip)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
      <Card title="Notes">
        {editable && notes !== null ? (
          <div className="space-y-2">
            <label className="sr-only" htmlFor="pd-notes">Notes</label>
            <Input id="pd-notes" maxLength={500} value={notes} onChange={(e) => setNotes(e.target.value)} />
            <div className="flex gap-2">
              <Button onClick={() => save({ notes })} loading={busy}>Save notes</Button>
              <Button variant="ghost" onClick={() => setNotes(null)}>Cancel</Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <p className="text-muted">{p.notes || "No notes."}</p>
            {editable && <Button variant="ghost" onClick={() => setNotes(p.notes)}>Edit notes</Button>}
          </div>
        )}
      </Card>
      {err != null && <ErrorState error={err} />}
      {editable && (
        <ConfirmButton
          title="Delete this promotion?"
          body={finished ? "Its measurement is no longer used to estimate future promotions." : "Forecasts on its dates go back to the model's numbers."}
          confirmLabel="Delete promotion"
          onConfirm={async () => {
            await api(`/promotions/${id}`, { method: "DELETE" });
            await mutate("/promotions");
            router.push("/promotions");
          }}
        >
          Delete promotion
        </ConfirmButton>
      )}
    </>
  );
}
