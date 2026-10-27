"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";

import { Button, ErrorState, Field, Select } from "@/components/ui";
import { api } from "@/lib/api";
import type { Product, Supplier } from "@/lib/types";

/** Shows a product's supplier and, for purchasing roles, lets them change it. */
export function SupplierPicker({ product, editable, onSaved }: { product: Product; editable: boolean; onSaved: () => void | Promise<void> }) {
  const suppliers = useSWR<Supplier[]>("/suppliers?include_inactive=true");
  const current = product.supplier_id ?? "";
  const [value, setValue] = useState(current);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const name = suppliers.data?.find((s) => s.id === current)?.name;

  if (!editable) {
    return <p className="text-sm text-muted">Supplier: {current ? (name ?? "…") : "none"}</p>;
  }
  const save = async () => {
    setErr(null);
    setBusy(true);
    try {
      await api("/suppliers/assign", { method: "POST", body: { supplier_id: value || null, product_ids: [product.id] } });
      await onSaved();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div>
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Supplier" htmlFor="sup" hint={<Link href="/suppliers" className="text-primary hover:underline">Manage suppliers</Link>}>
          <Select id="sup" value={value} onChange={(e) => setValue(e.target.value)} disabled={!suppliers.data}>
            <option value="">No supplier</option>
            {(suppliers.data ?? [])
              .filter((s) => s.is_active || s.id === current)
              .map((s) => <option key={s.id} value={s.id}>{s.name}{s.lead_time_days ? ` (${s.lead_time_days} d lead time)` : ""}</option>)}
          </Select>
        </Field>
        <Button onClick={() => void save()} loading={busy} disabled={value === current}>Update supplier</Button>
      </div>
      {err != null && <div className="mt-2"><ErrorState error={err} /></div>}
    </div>
  );
}
