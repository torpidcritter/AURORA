"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";

import { HealthBadge, useProductHealth } from "@/components/HealthBadge";
import { Badge, Button, Card, EmptyState, ErrorState, Input, LoadingBlock, Notice, PageHeader, Pagination, Select, Skeleton, Table, Td, Th } from "@/components/ui";
import { fmt1, fmtDate, fmtInt } from "@/lib/format";
import { matchesHealthFilter, type HealthFilter } from "@/lib/health";
import type { Page, Product } from "@/lib/types";

const LIMIT = 50;
/** The API cannot filter by health, so a health filter scans this many matching products (the API's page maximum). */
const SCAN_LIMIT = 500;

export default function ProductsPage() {
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");
  const [healthFilter, setHealthFilter] = useState<HealthFilter>("");
  const [sort, setSort] = useState("sku");
  const [order, setOrder] = useState<"asc" | "desc">("asc");
  const [offset, setOffset] = useState(0);
  const scanning = healthFilter !== "";
  const params = new URLSearchParams({ sort, order, limit: String(scanning ? SCAN_LIMIT : LIMIT), offset: String(scanning ? 0 : offset) });
  if (q.trim()) params.set("q", q.trim());
  if (category) params.set("category", category);
  const list = useSWR<Page<Product>>(`/products?${params.toString()}`, { keepPreviousData: true });
  const cats = useSWR<string[]>("/products/categories");
  const health = useProductHealth();

  const onSort = (k: string) => {
    if (k === sort) setOrder(order === "asc" ? "desc" : "asc");
    else {
      setSort(k);
      setOrder(k === "sku" || k === "name" ? "asc" : "desc");
    }
    setOffset(0);
  };
  const dir = (k: string) => (sort === k ? order : null);

  // keepPreviousData keeps the last page while a new one loads; a page fetched with the other limit (when the health
  // filter was just switched on or off) is not shown.
  const data = list.data && list.data.limit === (scanning ? SCAN_LIMIT : LIMIT) ? list.data : undefined;
  // With a health filter on, filter and paginate the scanned products here.
  const filtered = scanning && data && health.index ? data.items.filter((p) => matchesHealthFilter(health.index!.get(p.id), healthFilter)) : null;
  const rows = filtered ? filtered.slice(offset, offset + LIMIT) : (data?.items ?? []);
  const total = filtered ? filtered.length : (data?.total ?? 0);
  const truncated = scanning && (data?.total ?? 0) > SCAN_LIMIT;
  const filtering = Boolean(q || category || healthFilter);

  let body;
  if (list.error) body = <ErrorState error={list.error} onRetry={() => list.mutate()} />;
  else if (scanning && health.error != null) body = <ErrorState error={health.error} onRetry={health.retry} />;
  else if (!data || (scanning && !health.index)) body = <LoadingBlock rows={8} />;
  else if (rows.length === 0)
    body = (
      <EmptyState title={filtering ? "No matching products" : "No products yet"}>
        {filtering ? (
          healthFilter === "attention" ? "No product needs attention right now." : "Try a different search."
        ) : (
          <Link className="text-primary hover:underline" href="/upload">Upload sales history</Link>
        )}
      </EmptyState>
    );
  else
    body = (
      <>
        {truncated && (
          <div className="mb-3">
            <Notice tone="warn">
              The health filter checked the first {fmtInt(SCAN_LIMIT)} of {fmtInt(data.total)} matching products. Narrow the search or pick a category to check the rest.
            </Notice>
          </div>
        )}
        <Table caption="Products">
          <thead>
            <tr>
              <Th sort="sku" onSort={onSort} dir={dir("sku")}>SKU</Th>
              <Th sort="name" onSort={onSort} dir={dir("name")}>Name</Th>
              <Th>Category</Th>
              <Th>Model health</Th>
              <Th sort="units_30d" onSort={onSort} dir={dir("units_30d")} className="text-right">Units, 30 d</Th>
              <Th className="text-right">Avg / day</Th>
              <Th sort="on_hand" onSort={onSort} dir={dir("on_hand")} className="text-right">On hand</Th>
              <Th className="text-right">Lead time</Th>
              <Th sort="last_date" onSort={onSort} dir={dir("last_date")}>Last sale</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.id} className="hover:bg-white/5">
                <Td>
                  <Link href={`/products/${p.id}`} className="font-medium text-primary hover:underline">{p.sku}</Link>
                </Td>
                <Td>{p.name}</Td>
                <Td>{p.category ?? "—"}</Td>
                <Td>
                  {health.index ? (
                    <HealthBadge health={health.index.get(p.id)} />
                  ) : health.error != null ? (
                    <span className="text-faint" title="Model health could not be loaded">—</span>
                  ) : (
                    <Skeleton className="h-5 w-20" />
                  )}
                </Td>
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
        <Pagination total={total} limit={LIMIT} offset={offset} onChange={setOffset} />
      </>
    );

  const rules = health.index?.rules;
  return (
    <>
      <PageHeader title="Products" description="Every SKU found in your uploads, with recent demand, stock settings and how well its forecasts are holding up." />
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
          <label className="sr-only" htmlFor="ph">Model health</label>
          <Select
            id="ph"
            value={healthFilter}
            onChange={(e) => {
              setHealthFilter(e.target.value as HealthFilter);
              setOffset(0);
            }}
            className="max-w-xs"
          >
            <option value="">Any model health</option>
            <option value="attention">Needs attention{health.index ? ` (${health.index.attentionCount})` : ""}</option>
            <option value="healthy">Healthy</option>
            <option value="insufficient">Not enough data</option>
          </Select>
          {health.index && health.index.attentionCount > 0 && healthFilter !== "attention" && (
            <Button
              variant="ghost"
              onClick={() => {
                setHealthFilter("attention");
                setOffset(0);
              }}
            >
              Show {health.index.attentionCount} needing attention
            </Button>
          )}
        </div>
        {!scanning && health.error != null && (
          <div className="mb-3">
            <Notice tone="warn">
              Model health could not be loaded, so the health column is empty.{" "}
              <button type="button" className="underline underline-offset-2" onClick={health.retry}>
                Retry
              </button>
            </Notice>
          </div>
        )}
        {body}
        <p className="mt-4 text-xs text-faint">
          Model health combines live accuracy (forecasts that were served, scored against the sales that came in afterwards
          {rules ? `, over the last ${rules.windowDays} days; off target when ${rules.metric.toUpperCase()} is above ${rules.threshold}% with at least ${rules.minPoints} scored days` : ""}) with
          demand drift (last 30 days against up to 180 earlier days). Hover a badge for the details.
        </p>
      </Card>
    </>
  );
}
