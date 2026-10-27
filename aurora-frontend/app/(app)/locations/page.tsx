"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import useSWR from "swr";

import { Badge, Button, Card, EmptyState, ErrorState, Field, Input, LoadingBlock, PageHeader, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDateTime, fmtInt, fmtMoney } from "@/lib/format";
import { locationErrors } from "@/lib/locations";
import { useSession } from "@/lib/session";
import type { Page, StockLocation, StockTransfer } from "@/lib/types";

export default function LocationsPage() {
  const { can } = useSession();
  const admin = can("manage_settings");
  const [inactive, setInactive] = useState(false);
  const list = useSWR<StockLocation[]>(`/locations?include_inactive=${inactive}`);
  const transfers = useSWR<Page<StockTransfer>>("/stock/transfers?limit=20");
  const [f, setF] = useState({ code: "", name: "", address: "" });
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    const problem = locationErrors(f);
    if (problem) return setErr(new Error(problem));
    setBusy(true);
    try {
      await api("/locations", { method: "POST", body: { code: f.code.trim(), name: f.name.trim(), address: f.address.trim() || null } });
      setF({ code: "", name: "", address: "" });
      await list.mutate();
    } catch (e2) {
      setErr(e2);
    } finally {
      setBusy(false);
    }
  };

  const patch = async (loc: StockLocation, body: Record<string, unknown>) => {
    setErr(null);
    try {
      await api(`/locations/${loc.id}`, { method: "PATCH", body: { expected_version: loc.version, ...body } });
      await list.mutate();
    } catch (e) {
      setErr(e);
    }
  };

  return (
    <>
      <PageHeader
        title="Locations"
        description="Warehouses and stores where stock is kept. Counts, uploads, transfers and purchase order deliveries happen at a location; the product totals are the sum. Forecasts stay organisation-wide because sales are not recorded by location."
      />
      {admin && (
        <Card title="Add a location" className="mb-6">
          <form onSubmit={add} className="grid gap-3 md:grid-cols-4">
            <Field label="Code" htmlFor="lc" hint="Used in inventory uploads, e.g. WH-2"><Input id="lc" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} maxLength={40} /></Field>
            <Field label="Name" htmlFor="ln"><Input id="ln" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} maxLength={200} /></Field>
            <Field label="Address (optional)" htmlFor="la"><Input id="la" value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} maxLength={1000} /></Field>
            <div className="flex items-end"><Button type="submit" variant="primary" loading={busy}>Add location</Button></div>
          </form>
        </Card>
      )}
      {err != null && <div className="mb-4"><ErrorState error={err} /></div>}
      <Card
        title="All locations"
        className="mb-6"
        actions={
          <label className="flex items-center gap-2 text-xs text-muted">
            <input type="checkbox" checked={inactive} onChange={(e) => setInactive(e.target.checked)} />
            Show inactive
          </label>
        }
      >
        {list.error ? (
          <ErrorState error={list.error} onRetry={() => list.mutate()} />
        ) : !list.data ? (
          <LoadingBlock />
        ) : (
          <Table caption="Locations">
            <thead>
              <tr>
                <Th>Location</Th>
                <Th className="text-right">Products in stock</Th>
                <Th className="text-right">On hand</Th>
                <Th className="text-right">On order</Th>
                <Th className="text-right">Stock value</Th>
                {admin && <Th><span className="sr-only">Actions</span></Th>}
              </tr>
            </thead>
            <tbody>
              {list.data.map((loc) => (
                <tr key={loc.id}>
                  <Td>
                    <span className="font-medium text-ink">{loc.code}</span> <span className="text-muted">{loc.name}</span>{" "}
                    {loc.is_default && <Badge tone="info">default</Badge>} {!loc.is_active && <Badge tone="warn">inactive</Badge>}
                    {loc.address && <div className="text-xs text-faint">{loc.address}</div>}
                  </Td>
                  <Td className="text-right tabular-nums">{fmtInt(loc.products)}</Td>
                  <Td className="text-right tabular-nums">{fmtInt(loc.on_hand)}</Td>
                  <Td className="text-right tabular-nums">{fmtInt(loc.on_order)}</Td>
                  <Td className="text-right tabular-nums">{fmtMoney(loc.stock_value)}</Td>
                  {admin && (
                    <Td className="whitespace-nowrap text-right">
                      {!loc.is_default && loc.is_active && <Button variant="ghost" onClick={() => void patch(loc, { is_default: true })}>Make default</Button>}
                      {!loc.is_default && (
                        <Button variant="ghost" onClick={() => void patch(loc, { is_active: !loc.is_active })}>{loc.is_active ? "Deactivate" : "Reactivate"}</Button>
                      )}
                    </Td>
                  )}
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      <Card title="Recent transfers" subtitle="Move stock between locations from a product's page.">
        {transfers.error ? (
          <ErrorState error={transfers.error} onRetry={() => transfers.mutate()} />
        ) : !transfers.data ? (
          <LoadingBlock rows={2} />
        ) : transfers.data.items.length === 0 ? (
          <EmptyState title="No transfers yet" />
        ) : (
          <Table caption="Recent stock transfers">
            <thead>
              <tr>
                <Th>When</Th>
                <Th>SKU</Th>
                <Th>From</Th>
                <Th>To</Th>
                <Th className="text-right">Units</Th>
                <Th>Note</Th>
              </tr>
            </thead>
            <tbody>
              {transfers.data.items.map((t) => (
                <tr key={t.id}>
                  <Td className="whitespace-nowrap">{fmtDateTime(t.created_at)}</Td>
                  <Td><Link className="text-primary hover:underline" href={`/products/${t.product_id}`}>{t.sku}</Link></Td>
                  <Td>{t.from_code}</Td>
                  <Td>{t.to_code}</Td>
                  <Td className="text-right tabular-nums">{fmtInt(t.quantity)}</Td>
                  <Td className="text-muted">{t.note ?? ""}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
