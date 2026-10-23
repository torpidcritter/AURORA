"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";

import { Badge, Card, EmptyState, ErrorState, Input, LoadingBlock, PageHeader, Pagination, Select, Table, Td, Th } from "@/components/ui";
import { fmt1, fmtDate, fmtInt } from "@/lib/format";
import type { Page, Product } from "@/lib/types";

const LIMIT = 50;

export default function ProductsPage() {
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");
  const [sort, setSort] = useState("sku");
  const [order, setOrder] = useState<"asc" | "desc">("asc");
  const [offset, setOffset] = useState(0);
  const params = new URLSearchParams({ sort, order, limit: String(LIMIT), offset: String(offset) });
  if (q.trim()) params.set("q", q.trim());
  if (category) params.set("category", category);
  const list = useSWR<Page<Product>>(`/products?${params.toString()}`, { keepPreviousData: true });
  const cats = useSWR<string[]>("/products/categories");

  const onSort = (k: string) => {
    if (k === sort) setOrder(order === "asc" ? "desc" : "asc");
    else {
      setSort(k);
      setOrder(k === "sku" || k === "name" ? "asc" : "desc");
    }
    setOffset(0);
  };
  const dir = (k: string) => (sort === k ? order : null);

  return (
    <>
      <PageHeader title="Products" description="Every SKU found in your uploads, with recent demand and stock settings." />
      <Card>
        <div className="mb-4 flex flex-wrap gap-3">
          <label className="sr-only" htmlFor="pq">Search products</label>
          <Input
            id="pq"
            placeholder="Search SKU or name"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setOffset(0);
            }}
            className="max-w-xs"
          />
          <label className="sr-only" htmlFor="pc">Category</label>
          <Select
            id="pc"
            value={category}
            onChange={(e) => {
              setCategory(e.target.value);
              setOffset(0);
            }}
            className="max-w-xs"
          >
            <option value="">All categories</option>
            {(cats.data ?? []).map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </Select>
        </div>
        {list.error ? (
          <ErrorState error={list.error} onRetry={() => list.mutate()} />
        ) : !list.data ? (
          <LoadingBlock rows={8} />
        ) : list.data.items.length === 0 ? (
          <EmptyState title={q || category ? "No matching products" : "No products yet"}>
            {q || category ? "Try a different search." : <Link className="text-primary hover:underline" href="/upload">Upload sales history</Link>}
          </EmptyState>
        ) : (
          <>
            <Table caption="Products">
              <thead>
                <tr>
                  <Th sort="sku" onSort={onSort} dir={dir("sku")}>SKU</Th>
                  <Th sort="name" onSort={onSort} dir={dir("name")}>Name</Th>
                  <Th>Category</Th>
                  <Th sort="units_30d" onSort={onSort} dir={dir("units_30d")} className="text-right">Units, 30 d</Th>
                  <Th className="text-right">Avg / day</Th>
                  <Th sort="on_hand" onSort={onSort} dir={dir("on_hand")} className="text-right">On hand</Th>
                  <Th className="text-right">Lead time</Th>
                  <Th sort="last_date" onSort={onSort} dir={dir("last_date")}>Last sale</Th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((p) => (
                  <tr key={p.id} className="hover:bg-white/5">
                    <Td>
                      <Link href={`/products/${p.id}`} className="font-medium text-primary hover:underline">{p.sku}</Link>
                    </Td>
                    <Td>{p.name}</Td>
                    <Td>{p.category ?? "—"}</Td>
                    <Td className="text-right tabular-nums">{fmtInt(p.units_30d)}</Td>
                    <Td className="text-right tabular-nums">{fmt1(p.avg_daily_30d)}</Td>
                    <Td className="text-right tabular-nums">
                      {p.inventory_known ? fmtInt(p.on_hand) : <Badge tone="warn">Unknown</Badge>}
                    </Td>
                    <Td className="text-right tabular-nums">{p.lead_time_days == null ? "default" : `${p.lead_time_days} d`}</Td>
                    <Td>{fmtDate(p.last_date)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <Pagination total={list.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
          </>
        )}
      </Card>
    </>
  );
}
