"use client";

import useSWR from "swr";

import { Select } from "@/components/ui";
import type { Page, Product } from "@/lib/types";

export function ProductPicker({ id, value, onChange }: { id: string; value: string; onChange: (id: string) => void }) {
  const list = useSWR<Page<Product>>("/products?limit=500&sort=sku");
  return (
    <Select id={id} value={value} onChange={(e) => onChange(e.target.value)} disabled={!list.data}>
      <option value="">{list.data ? (list.data.items.length ? "Choose a product" : "No products") : "Loading…"}</option>
      {(list.data?.items ?? []).map((p) => (
        <option key={p.id} value={p.id}>
          {p.sku} · {p.name}
        </option>
      ))}
    </Select>
  );
}
