"use client";

import { useState } from "react";
import useSWR from "swr";

import { SupplierForm } from "@/components/SupplierForm";
import { Badge, Button, Card, EmptyState, ErrorState, Input, LoadingBlock, PageHeader, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtInt, fmtMoney } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Supplier } from "@/lib/types";

export default function SuppliersPage() {
  const { can } = useSession();
  const editable = can("manage_purchasing");
  const [q, setQ] = useState("");
  const [inactive, setInactive] = useState(false);
  const list = useSWR<Supplier[]>(`/suppliers?include_inactive=${inactive}${q.trim() ? `&q=${encodeURIComponent(q.trim())}` : ""}`);
  const [editing, setEditing] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);

  const toggleActive = async (s: Supplier) => {
    setErr(null);
    try {
      await api(`/suppliers/${s.id}`, { method: "PATCH", body: { expected_version: s.version, is_active: !s.is_active } });
      await list.mutate();
    } catch (e) {
      setErr(e);
    }
  };

  return (
    <>
      <PageHeader
        title="Suppliers"
        description="Who you buy from. A supplier's lead time is used for its products that have none of their own, and purchase orders are grouped by supplier."
      />
      {editable && (
        <Card title="Add a supplier" className="mb-6">
          <SupplierForm onSaved={async () => void (await list.mutate())} />
        </Card>
      )}
      <Card
        title="All suppliers"
        actions={
          <div className="flex items-center gap-3">
            <label className="sr-only" htmlFor="sq">Search suppliers</label>
            <Input id="sq" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} className="w-44" />
            <label className="flex items-center gap-2 text-xs text-muted">
              <input type="checkbox" checked={inactive} onChange={(e) => setInactive(e.target.checked)} />
              Show inactive
            </label>
          </div>
        }
      >
        {err != null && <div className="mb-3"><ErrorState error={err} /></div>}
        {list.error ? (
          <ErrorState error={list.error} onRetry={() => list.mutate()} />
        ) : !list.data ? (
          <LoadingBlock />
        ) : list.data.length === 0 ? (
          <EmptyState title="No suppliers yet">Add one above, then link products to it from each product page.</EmptyState>
        ) : (
          <Table caption="Suppliers">
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>Contact</Th>
                <Th className="text-right">Lead time</Th>
                <Th className="text-right">Min. order</Th>
                <Th className="text-right">Products</Th>
                {editable && <Th><span className="sr-only">Actions</span></Th>}
              </tr>
            </thead>
            <tbody>
              {list.data.map((s) =>
                editing === s.id ? (
                  <tr key={s.id}>
                    <Td colSpan={editable ? 6 : 5}>
                      <SupplierForm
                        supplier={s}
                        onCancel={() => setEditing(null)}
                        onSaved={async () => {
                          setEditing(null);
                          await list.mutate();
                        }}
                      />
                    </Td>
                  </tr>
                ) : (
                  <tr key={s.id}>
                    <Td>
                      <span className="font-medium text-ink">{s.name}</span> {!s.is_active && <Badge tone="warn">inactive</Badge>}
                      {s.notes && <div className="text-xs text-faint">{s.notes}</div>}
                    </Td>
                    <Td>
                      <div>{s.contact_name ?? "—"}</div>
                      <div className="text-xs text-faint">{[s.email, s.phone].filter(Boolean).join(" · ")}</div>
                    </Td>
                    <Td className="text-right tabular-nums">{s.lead_time_days == null ? "—" : `${s.lead_time_days} d`}</Td>
                    <Td className="text-right tabular-nums">{fmtMoney(s.min_order_value)}</Td>
                    <Td className="text-right tabular-nums">{fmtInt(s.product_count)}</Td>
                    {editable && (
                      <Td className="whitespace-nowrap text-right">
                        <Button variant="ghost" onClick={() => setEditing(s.id)}>Edit</Button>
                        <Button variant="ghost" onClick={() => void toggleActive(s)}>{s.is_active ? "Deactivate" : "Reactivate"}</Button>
                      </Td>
                    )}
                  </tr>
                ),
              )}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
