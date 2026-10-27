"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import useSWR from "swr";

import { Badge, Button, ErrorState, Field, Input, LoadingBlock, Select, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtInt } from "@/lib/format";
import { transferErrors } from "@/lib/locations";
import type { Product, ProductStock } from "@/lib/types";

/** Stock of one product at each location, with per-location counts and transfers for roles that manage inventory. */
export function StockByLocation({ product, editable, onChanged }: { product: Product; editable: boolean; onChanged: () => void | Promise<void> }) {
  const stock = useSWR<ProductStock>(`/products/${product.id}/stock`);
  const [counting, setCounting] = useState<string | null>(null);
  const [count, setCount] = useState("");
  const [t, setT] = useState({ from: "", to: "", quantity: "", note: "" });
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);

  if (stock.error) return <ErrorState error={stock.error} onRetry={() => stock.mutate()} />;
  if (!stock.data) return <LoadingBlock rows={2} />;
  const rows = stock.data.locations;
  const active = rows.filter((r) => r.is_active);

  const after = async () => {
    await stock.mutate();
    await onChanged();
  };

  const saveCount = async (locationId: string) => {
    setErr(null);
    if (!/^\d+$/.test(count.trim())) return setErr(new Error("Enter a whole number of units"));
    setBusy("count");
    try {
      await api(`/products/${product.id}/inventory`, { method: "PATCH", body: { expected_version: product.version, location_id: locationId, on_hand: Number(count) } });
      setCounting(null);
      await after();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  };

  const submitTransfer = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    const problem = transferErrors(t, rows);
    if (problem) return setErr(new Error(problem));
    setBusy("transfer");
    try {
      await api("/stock/transfers", {
        method: "POST",
        body: { product_id: product.id, from_location_id: t.from, to_location_id: t.to, quantity: Number(t.quantity), note: t.note.trim() || null },
      });
      setT({ from: "", to: "", quantity: "", note: "" });
      await after();
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      <Table caption="Stock by location">
        <thead>
          <tr>
            <Th>Location</Th>
            <Th className="text-right">On hand</Th>
            <Th className="text-right">On order</Th>
            {editable && <Th><span className="sr-only">Count</span></Th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.location_id}>
              <Td>
                <span className="font-medium text-ink">{r.code}</span> <span className="text-muted">{r.name}</span>{" "}
                {r.is_default && <Badge tone="info">default</Badge>} {!r.is_active && <Badge tone="warn">inactive</Badge>}
              </Td>
              <Td className="text-right tabular-nums">
                {counting === r.location_id ? (
                  <span className="inline-flex items-center gap-2">
                    <label className="sr-only" htmlFor={`cnt-${r.location_id}`}>Counted units at {r.code}</label>
                    <Input id={`cnt-${r.location_id}`} inputMode="numeric" value={count} onChange={(e) => setCount(e.target.value)} className="w-24" />
                    <Button variant="primary" loading={busy === "count"} onClick={() => void saveCount(r.location_id)}>Save count</Button>
                  </span>
                ) : (
                  fmtInt(r.on_hand)
                )}
              </Td>
              <Td className="text-right tabular-nums">{fmtInt(r.on_order)}</Td>
              {editable && (
                <Td className="text-right">
                  {r.is_active && counting !== r.location_id && (
                    <Button
                      variant="ghost"
                      onClick={() => {
                        setCounting(r.location_id);
                        setCount(String(r.on_hand));
                      }}
                    >
                      Count
                    </Button>
                  )}
                  {counting === r.location_id && <Button variant="ghost" onClick={() => setCounting(null)}>Cancel</Button>}
                </Td>
              )}
            </tr>
          ))}
          <tr>
            <Td className="font-medium">Total</Td>
            <Td className="text-right font-medium tabular-nums">{fmtInt(stock.data.on_hand)}</Td>
            <Td className="text-right font-medium tabular-nums">{fmtInt(stock.data.on_order)}</Td>
            {editable && <Td />}
          </tr>
        </tbody>
      </Table>
      {editable && active.length > 1 && (
        <form onSubmit={submitTransfer} className="mt-4 grid gap-3 sm:grid-cols-4" aria-label="Transfer stock">
          <Field label="Move from" htmlFor="tr-from">
            <Select id="tr-from" value={t.from} onChange={(e) => setT({ ...t, from: e.target.value })}>
              <option value="">Choose</option>
              {rows.filter((r) => r.on_hand > 0).map((r) => <option key={r.location_id} value={r.location_id}>{r.code} ({r.on_hand})</option>)}
            </Select>
          </Field>
          <Field label="Move to" htmlFor="tr-to">
            <Select id="tr-to" value={t.to} onChange={(e) => setT({ ...t, to: e.target.value })}>
              <option value="">Choose</option>
              {active.filter((r) => r.location_id !== t.from).map((r) => <option key={r.location_id} value={r.location_id}>{r.code}</option>)}
            </Select>
          </Field>
          <Field label="Units" htmlFor="tr-q"><Input id="tr-q" inputMode="numeric" value={t.quantity} onChange={(e) => setT({ ...t, quantity: e.target.value })} /></Field>
          <div className="flex items-end"><Button type="submit" loading={busy === "transfer"}>Transfer</Button></div>
          <div className="sm:col-span-4"><Field label="Note (optional)" htmlFor="tr-n"><Input id="tr-n" value={t.note} onChange={(e) => setT({ ...t, note: e.target.value })} maxLength={500} /></Field></div>
        </form>
      )}
      {err != null && <div className="mt-3"><ErrorState error={err} /></div>}
      <p className="mt-3 text-xs text-faint">
        Forecasts and reorder suggestions use the total, because sales are not recorded by location. <Link href="/locations" className="text-primary hover:underline">Manage locations</Link>
      </p>
    </div>
  );
}
